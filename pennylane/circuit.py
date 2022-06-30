# Copyright 2018-2022 Xanadu Quantum Technologies Inc.

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from collections import Counter, defaultdict
from copy import copy
import functools

import pennylane as qml
from pennylane.wires import Wires
from pennylane.measurements import Sample, Probability, State, Expectation, Variance
from pennylane.operation import DiagGatesUndefinedError
from pennylane.queuing import QueueManager, AnnotatedQueue, process_queue

OPENQASM_GATES = {
    "CNOT": "cx",
    "CZ": "cz",
    "U3": "u3",
    "U2": "u2",
    "U1": "u1",
    "Identity": "id",
    "PauliX": "x",
    "PauliY": "y",
    "PauliZ": "z",
    "Hadamard": "h",
    "S": "s",
    "S.inv": "sdg",
    "T": "t",
    "T.inv": "tdg",
    "RX": "rx",
    "RY": "ry",
    "RZ": "rz",
    "CRX": "crx",
    "CRY": "cry",
    "CRZ": "crz",
    "SWAP": "swap",
    "Toffoli": "ccx",
    "CSWAP": "cswap",
    "PhaseShift": "u1",
}
"""
dict[str, str]: Maps PennyLane gate names to equivalent QASM gate names.

Note that QASM has two native gates:

- ``U`` (equivalent to :class:`~.U3`)
- ``CX`` (equivalent to :class:`~.CNOT`)

All other gates are defined in the file stdgates.inc:
https://github.com/Qiskit/openqasm/blob/master/examples/stdgates.inc
"""


class TapeError(ValueError):
    """An error raised with a quantum tape."""


def expand_circuit(circuit, depth=1, stop_at=None, expand_measurements=False):
    """Expand all objects in a tape to a specific depth.

    Args:
        depth (int): the depth the tape should be expanded
        stop_at (Callable): A function which accepts a queue object,
            and returns ``True`` if this object should *not* be expanded.
            If not provided, all objects that support expansion will be expanded.
        expand_measurements (bool): If ``True``, measurements will be expanded
            to basis rotations and computational basis measurements.

    **Example**

    Consider the following nested tape:

    .. code-block:: python

        with QuantumTape() as tape:
            qml.BasisState(np.array([1, 1]), wires=[0, 'a'])

            with QuantumTape() as tape2:
                qml.Rot(0.543, 0.1, 0.4, wires=0)

            qml.CNOT(wires=[0, 'a'])
            qml.RY(0.2, wires='a')
            qml.probs(wires=0), qml.probs(wires='a')

    The nested structure is preserved:

    >>> tape.operations
    [BasisState(array([1, 1]), wires=[0, 'a']),
     <QuantumTape: wires=[0], params=3>,
     CNOT(wires=[0, 'a']),
     RY(0.2, wires=['a'])]

    Calling ``expand_circuit`` will return a tape with all nested tapes
    expanded, resulting in a single tape of quantum operations:

    >>> new_tape = qml.tape.tape.expand_circuit(tape)
    >>> new_tape.operations
    [BasisStatePreparation([1, 1], wires=[0, 'a']),
    Rot(0.543, 0.1, 0.4, wires=[0]),
    CNOT(wires=[0, 'a']),
    RY(0.2, wires=['a'])]
    """
    if depth == 0:
        return circuit

    # by default expand all objects
    stop_at = (lambda obj: False) if stop_at is None else stop_at

    new_ops = []
    for op in circuit.operations:
        if stop_at(op):
            new_ops.append(op)
            continue

        try:
            expansion = op.expand()
        except qml.operation.DecompositionUndefinedError:
            new_ops.append(op)
            continue
        expanded_expansion = expand_circuit(expansion, stop_at=stop_at, depth=depth - 1)
        new_ops += expanded_expansion.circuit

    new_measurements = list
    if expand_measurements:
        if len(circuit._obs_sharing_wires) > 0:
            try:
                rotations, diag_obs = qml.grouping.diagonalize_qwc_pauli_words(
                    circuit._obs_sharing_wires
                )
            except (TypeError, ValueError) as e:
                raise qml.QuantumFunctionError(
                    "Only observables that are qubit-wise commuting "
                    "Pauli words can be returned on the same wire"
                ) from e

            new_ops += rotations

            for observable, idx in zip(diag_obs, circuit._obs_sharing_wires_id):
                new_measurements[idx] = qml.measurements.MeasurementProcess(
                    circuit.measurements[i].return_type, obs=observable
                )

    return Circuit(new_ops, new_measurements)


def make_circuit(obj, *args, **kwargs):
    if isinstance(obj, Circuit):
        return obj

    if isinstance(obj, qml.transforms.TransformedQfunc):
        return obj(*args, **kwargs)

    # else is qfunc
    with QueueManager.stop_recording(), AnnotatedQueue() as queue:
        output = obj(*args, **kwargs)
    operations, measurements = process_queue(queue)
    circuit = qml.Circuit(operations, measurements)
    circuit._qfunc_output = output

    return circuit


class Circuit:

    _queue_category = "_ops"

    def __init__(self, ops, measurements, name=None):
        self.name = name

        self._ops = tuple(ops)
        self._measurements = tuple(measurements)
        self._observables = None
        self._par_info = {}

        self._trainable_params = []
        self._graph = None
        self._specs = None
        self._depth = None
        self._output_dim = 0
        self._batch_size = 0
        self._qfunc_output = None

        self.wires = Wires([])
        self.num_wires = 0

        self.is_sampled = False
        self.all_sampled = False
        self.inverse = False

        self._obs_sharing_wires = []
        """list[.Observable]: subset of the observables that share wires with another observable,
        i.e., that do not have their own unique set of wires."""
        self._obs_sharing_wires_id = []

        self._update()

    def __repr__(self):
        return f"<{self.__class__.__name__}: wires={self.wires.tolist()}, params={self.num_params}>"

    @property
    def operations(self):
        return self._ops

    @property
    def measurements(self):
        return self._measurements

    @property
    def observables(self):
        if self._observables is None:
            self._observables = tuple(m.obs if m.obs is not None else m for m in self._measurements)
        return self._observables

    @property
    def num_params(self):
        return len(self.trainable_params)

    @property
    def batch_size(self):
        return self._batch_size

    @property
    def output_dim(self):
        return self._output_dim

    @property
    def diagonalizing_gates(self):
        rotation_gates = []

        for observable in self.observables:
            # some observables do not have diagonalizing gates,
            # in which case we just don't append any
            try:
                rotation_gates.extend(observable.diagonalizing_gates())
            except DiagGatesUndefinedError:
                pass

        return rotation_gates

    @property
    def circuit(self):
        """Returns the quantum circuit recorded by the tape.

        The circuit is created with the assumptions that:

        * The ``operations`` attribute contains quantum operations and
          mid-circuit measurements and
        * The ``measurements`` attribute contains terminal measurements.

        Note that the resulting list could contain MeasurementProcess objects
        that some devices may not support.

        Returns:

            list[.Operator, .MeasurementProcess]: the quantum circuit
            containing quantum operations and measurements as recorded by the
            tape.
        """
        return self.operations + self.measurements

    def __iter__(self):
        """list[.Operator, .MeasurementProcess]: Return an iterator to the
        underlying quantum circuit object."""
        return iter(self.circuit)

    def __getitem__(self, idx):
        """list[.Operator]: Return the indexed operator from underlying quantum
        circuit object."""
        return self.circuit[idx]

    def __len__(self):
        """int: Return the number of operations and measurements in the
        underlying quantum circuit object."""
        return len(self.circuit)

    @property
    def interface(self):
        """str, None: automatic differentiation interface used by the quantum tape (if any)"""
        return None

    # Update Methods #####################

    def _update_circuit_info(self):
        """Update circuit metadata"""
        self.wires = Wires.all_wires([op.wires for op in self.operations + self.measurements])
        self.num_wires = len(self.wires)

        self.is_sampled = any(m.return_type is Sample for m in self.measurements)
        self.all_sampled = all(m.return_type is Sample for m in self.measurements)

    def _update_batch_size(self):
        """Infer the batch_size from the batch sizes of the tape operations and
        check the latter for consistency."""
        candidate = None
        for op in self.operations:
            op_batch_size = getattr(op, "batch_size", None)
            if op_batch_size is None:
                continue
            if candidate and op_batch_size != candidate:
                raise TapeError(
                    "The batch sizes of the tape operations do not match, they include "
                    f"{candidate} and {op_batch_size}."
                )
            candidate = candidate or op_batch_size

        self._batch_size = candidate

    def _update_output_dim(self):
        self._output_dim = 0
        for m in self.measurements:
            # attempt to infer the output dimension
            if m.return_type is Probability:
                # TODO: what if we had a CV device here? Having the base as
                # 2 would have to be swapped to the cutoff value
                self._output_dim += 2 ** len(m.wires)
            elif m.return_type is not State:
                self._output_dim += 1
        if self.batch_size:
            self._output_dim *= self.batch_size

    def _update_observables(self):
        """Update information about observables, including the wires that are acted upon and
        identifying any observables that share wires"""
        obs_wires = [wire for m in self.measurements for wire in m.wires if m.obs is not None]
        self._obs_sharing_wires = []
        self._obs_sharing_wires_id = []

        for m in self._measurements:
            if m.obs is not None:
                m.obs.return_type = m.return_type

        if len(obs_wires) != len(set(obs_wires)):
            c = Counter(obs_wires)
            repeated_wires = {w for w in obs_wires if c[w] > 1}

            for i, m in enumerate(self.measurements):
                if m.obs is not None:
                    if len(set(m.wires) & repeated_wires) > 0:
                        self._obs_sharing_wires.append(m.obs)
                        self._obs_sharing_wires_id.append(i)

    def _update_par_info(self):
        """Update the parameter information dictionary"""
        param_count = 0

        for obj in self.operations + self.observables:

            for p in range(len(obj.data)):
                info = self._par_info.get(param_count, {})
                info.update({"op": obj, "p_idx": p})

                self._par_info[param_count] = info
                param_count += 1

    def _update_trainable_params(self):
        """Set the trainable parameters

        self._par_info.keys() is assumed to be sorted
        As its order is maintained, this assumes that self._par_info
        is created in a sorted manner, as in _update_par_info
        """
        self._trainable_params = list(self._par_info)

    def _update(self):
        """Update all internal tape metadata regarding processed operations and observables"""
        self._graph = None
        self._specs = None
        self._depth = None
        self._update_circuit_info()
        self._update_par_info()
        self._update_trainable_params()
        self._update_observables()
        self._update_batch_size()
        self._update_output_dim()

    # TAPE MODIFICATION METHODS ########################

    def expand(self, depth=1, stop_at=None, expand_measurements=False):
        new_tape = expand_circuit(
            self, depth=depth, stop_at=stop_at, expand_measurements=expand_measurements
        )
        new_tape._update()
        return new_tape

    def inv(self):
        # we must remap the old parameter
        # indices to the new ones after the operation order is reversed.
        parameter_indices = []
        param_count = 0

        prep_ops_names = {"BasisState", "QubitStateVector", "QubitDensityMatrix"}

        prep_ops = [op for op in self.operations if op.name in prep_ops_names]
        non_prep_ops = [op for op in self.operations if op.name not in prep_ops_names]

        for queue in [prep_ops, non_prep_ops, self.observables]:
            # iterate through all queues

            obj_params = []

            for obj in queue:
                # index the number of parameters on each operation
                num_obj_params = len(obj.data)
                obj_params.append(list(range(param_count, param_count + num_obj_params)))

                # keep track of the total number of parameters encountered so far
                param_count += num_obj_params

            if queue == self._ops:
                # reverse the list representing operator parameters
                obj_params = obj_params[::-1]

            parameter_indices.extend(obj_params)

        # flatten the list of parameter indices after the reversal
        parameter_indices = [item for sublist in parameter_indices for item in sublist]
        parameter_mapping = dict(zip(parameter_indices, range(len(parameter_indices))))

        # map the params
        self.trainable_params = [parameter_mapping[i] for i in self.trainable_params]
        self._par_info = {parameter_mapping[k]: v for k, v in self._par_info.items()}

        new_ops = []
        for idx, op in enumerate(non_prep_ops):
            try:
                new_ops.append(op.adjoint())
            except qml.operation.AdjointUndefinedError:
                new_ops.append(op.inv())

        self._ops = tuple(prep_ops + list(reversed(new_ops)))

    def adjoint(self):
        new_tape = self.copy(copy_operations=True)
        new_tape.inv()

        return new_tape

    def unwrap(self):
        return qml.tape.UnwrapTape(self)

    def copy(self, copy_operations=False):
        """Returns a shallow copy of the quantum tape.

        Args:
            copy_operations (bool): If True, the tape operations are also shallow copied.
                Otherwise, if False, the copied tape operations will simply be references
                to the original tape operations; changing the parameters of one tape will likewise
                change the parameters of all copies.

        Returns:
            .QuantumTape: a shallow copy of the tape
        """

        if copy_operations:
            _ops = [copy(op) for op in self._ops]
            _measurements = [copy(m) for m in self._measurements]
        else:
            _ops = copy(self._ops)
            _measurements = copy(self._measurements)

        new_circuit = Circuit(_ops, _measurements)
        new_circuit.trainable_params = self.trainable_params.copy()
        new_circuit._output_dim = self.output_dim

        return new_circuit

    def __copy__(self):
        return self.copy(copy_operations=True)

    # PARAMETERS ######################################

    @property
    def trainable_params(self):
        """Store or return a list containing the indices of parameters that support
        differentiability. The indices provided match the order of appearence in the
        quantum circuit.

        Setting this property can help reduce the number of quantum evaluations needed
        to compute the Jacobian; parameters not marked as trainable will be
        automatically excluded from the Jacobian computation.

        The number of trainable parameters determines the number of parameters passed to
        :meth:`~.set_parameters`, and changes the default output size of method :meth:`~.get_parameters()`.

        .. note::

            For devices that support native backpropagation (such as
            ``default.qubit.tf`` and ``default.qubit.autograd``), this
            property contains no relevant information when using
            backpropagation to compute gradients.

        **Example**

        .. code-block:: python

            with QuantumTape() as tape:
                qml.RX(0.432, wires=0)
                qml.RY(0.543, wires=0)
                qml.CNOT(wires=[0, 'a'])
                qml.RX(0.133, wires='a')
                qml.expval(qml.PauliZ(wires=[0]))

        >>> tape.trainable_params
        [0, 1, 2]
        >>> tape.trainable_params = [0] # set only the first parameter as trainable
        >>> tape.get_parameters()
        [0.432]
        """
        return self._trainable_params

    @trainable_params.setter
    def trainable_params(self, param_indices):
        """Store the indices of parameters that support differentiability.

        Args:
            param_indices (list[int]): parameter indices
        """
        if any(not isinstance(i, int) or i < 0 for i in param_indices):
            raise TapeError("Argument indices must be non-negative integers.")

        if any(i > len(self._par_info) for i in param_indices):
            raise TapeError(f"Tape has at most {self.num_params} parameters.")

        self._trainable_params = sorted(set(param_indices))

    def get_operation(self, idx):
        """Returns the trainable operation, and the corresponding operation argument
        index, for a specified trainable parameter index.

        Args:
            idx (int): the trainable parameter index

        Returns:
            tuple[.Operation, int]: tuple containing the corresponding
            operation, and an integer representing the argument index,
            for the provided trainable parameter.
        """
        # get the index of the parameter in the tape
        t_idx = self.trainable_params[idx]

        # get the info for the parameter
        info = self._par_info[t_idx]

        # get the corresponding operation
        op = info["op"]

        # get the corresponding operation parameter index
        # (that is, index of the parameter within the operation)
        p_idx = info["p_idx"]
        return op, p_idx

    def get_parameters(
        self, trainable_only=True, operations_only=False, **kwargs
    ):  # pylint:disable=unused-argument
        """Return the parameters incident on the tape operations.

        The returned parameters are provided in order of appearance
        on the tape.

        Args:
            trainable_only (bool): if True, returns only trainable parameters
            operations_only (bool): if True, returns only the parameters of the
                operations excluding parameters to observables of measurements

        **Example**

        .. code-block:: python

            with QuantumTape() as tape:
                qml.RX(0.432, wires=0)
                qml.RY(0.543, wires=0)
                qml.CNOT(wires=[0, 'a'])
                qml.RX(0.133, wires='a')
                qml.expval(qml.PauliZ(wires=[0]))

        By default, all parameters are trainable and will be returned:

        >>> tape.get_parameters()
        [0.432, 0.543, 0.133]

        Setting the trainable parameter indices will result in only the specified
        parameters being returned:

        >>> tape.trainable_params = [1] # set the second parameter as trainable
        >>> tape.get_parameters()
        [0.543]

        The ``trainable_only`` argument can be set to ``False`` to instead return
        all parameters:

        >>> tape.get_parameters(trainable_only=False)
        [0.432, 0.543, 0.133]
        """
        params = []
        iterator = self.trainable_params if trainable_only else self._par_info

        for p_idx in iterator:
            op = self._par_info[p_idx]["op"]
            if operations_only and hasattr(op, "return_type"):
                continue

            op_idx = self._par_info[p_idx]["p_idx"]
            params.append(op.data[op_idx])
        return params

    def set_parameters(self, params, trainable_only=True):
        """Set the parameters incident on the tape operations.

        Args:
            params (list[float]): A list of real numbers representing the
                parameters of the quantum operations. The parameters should be
                provided in order of appearance in the quantum tape.
            trainable_only (bool): if True, set only trainable parameters

        **Example**

        .. code-block:: python

            with QuantumTape() as tape:
                qml.RX(0.432, wires=0)
                qml.RY(0.543, wires=0)
                qml.CNOT(wires=[0, 'a'])
                qml.RX(0.133, wires='a')
                qml.expval(qml.PauliZ(wires=[0]))

        By default, all parameters are trainable and can be modified:

        >>> tape.set_parameters([0.1, 0.2, 0.3])
        >>> tape.get_parameters()
        [0.1, 0.2, 0.3]

        Setting the trainable parameter indices will result in only the specified
        parameters being modifiable. Note that this only modifies the number of
        parameters that must be passed.

        >>> tape.trainable_params = [0, 2] # set the first and third parameter as trainable
        >>> tape.set_parameters([-0.1, 0.5])
        >>> tape.get_parameters(trainable_only=False)
        [-0.1, 0.2, 0.5]

        The ``trainable_only`` argument can be set to ``False`` to instead set
        all parameters:

        >>> tape.set_parameters([4, 1, 6], trainable_only=False)
        >>> tape.get_parameters(trainable_only=False)
        [4, 1, 6]
        """
        if trainable_only:
            iterator = zip(self.trainable_params, params)
            required_length = self.num_params
        else:
            iterator = enumerate(params)
            required_length = len(self._par_info)

        if len(params) != required_length:
            raise TapeError("Number of provided parameters does not match.")

        for idx, p in iterator:
            op = self._par_info[idx]["op"]
            op.data[self._par_info[idx]["p_idx"]] = p
            op._check_batching(op.data)
        self._update_batch_size()
        self._update_output_dim()

    @property
    def data(self):
        """Alias to :meth:`~.get_parameters` and :meth:`~.set_parameters`
        for backwards compatibilities with operations."""
        return self.get_parameters(trainable_only=False)

    @data.setter
    def data(self, params):
        self.set_parameters(params, trainable_only=False)

    # MEASUREMENT SHAPE ###################################

    @staticmethod
    def _single_measurement_shape(measurement_process, device):
        """Auxiliary function of shape that determines the output
        shape of a tape with a single measurement.

        Args:
            measurement_process (MeasurementProcess): the measurement process
                associated with the single measurement
            device (~.Device): a PennyLane device

        Returns:
            tuple: output shape
        """
        return measurement_process.shape(device)

    @staticmethod
    def _multi_homogenous_measurement_shape(mps, device):
        """Auxiliary function of shape that determines the output
        shape of a tape with multiple homogenous measurements.

        .. note::

            Assuming multiple probability measurements where not all
            probability measurements have the same number of wires specified,
            the output shape of the tape is a sum of the output shapes produced
            by each probability measurement.

            Consider the `qml.probs(wires=[0]), qml.probs(wires=[1,2])`
            multiple probability measurement with an analytic device as an
            example.

            The output shape will be a one element tuple `(6,)`, where the
            element `6` is equal to `2 ** 1 + 2 ** 2 = 6`. The base of each
            term is determined by the number of basis states and the exponent
            of each term comes from the length of the wires specified for the
            probability measurements: `1 == len([0]) and 2 == len([1, 2])`.
        """
        shape = tuple()

        # We know that there's one type of return_type, gather it from the
        # first one
        ret_type = mps[0].return_type
        if ret_type == State:
            raise TapeError(
                "Getting the output shape of a tape with multiple state measurements is not supported."
            )

        shot_vector = device._shot_vector
        if shot_vector is None:
            if ret_type in (Expectation, Variance):

                shape = (len(mps),)

            elif ret_type == Probability:

                wires_num_set = {len(meas.wires) for meas in mps}
                same_num_wires = len(wires_num_set) == 1
                if same_num_wires:
                    # All probability measurements have the same number of
                    # wires, gather the length from the first one

                    len_wires = len(mps[0].wires)
                    dim = mps[0]._get_num_basis_states(len_wires, device)
                    shape = (len(mps), dim)

                else:
                    # There are a varying number of wires that the probability
                    # measurement processes act on
                    shape = (sum(2 ** len(m.wires) for m in mps),)

            elif ret_type == Sample:

                shape = (len(mps), device.shots)

            # No other measurement type to check

        else:
            shape = Circuit._shape_shot_vector_multi_homogenous(mps, device)

        return shape

    @staticmethod
    def _shape_shot_vector_multi_homogenous(mps, device):
        """Auxiliary function for determining the output shape of the tape for
        multiple homogenous measurements for a device with a shot vector.

        Note: it is assumed that getting the output shape of a tape with
        multiple state measurements is not supported.
        """
        shape = tuple()

        ret_type = mps[0].return_type
        shot_vector = device._shot_vector

        # Shot vector was defined
        if ret_type in (Expectation, Variance):
            num = sum(shottup.copies for shottup in shot_vector)
            shape = (num, len(mps))

        elif ret_type == Probability:

            wires_num_set = {len(meas.wires) for meas in mps}
            same_num_wires = len(wires_num_set) == 1
            if same_num_wires:
                # All probability measurements have the same number of
                # wires, gather the length from the first one

                len_wires = len(mps[0].wires)
                dim = mps[0]._get_num_basis_states(len_wires, device)
                shot_copies_sum = sum(s.copies for s in shot_vector)
                shape = (shot_copies_sum, len(mps), dim)

            else:
                # There is a varying number of wires that the probability
                # measurement processes act on
                # TODO: revisit when issues with this case are resolved
                raise TapeError(
                    "Getting the output shape of a tape with multiple probability measurements "
                    "along with a device that defines a shot vector is not supported."
                )

        elif ret_type == Sample:
            shape = []
            for shot_val in device.shot_vector:
                for _ in range(shot_val.copies):
                    shots = shot_val.shots
                    if shots != 1:
                        shape.append(tuple([shots, len(mps)]))
                    else:
                        shape.append((len(mps),))
        return shape

    def shape(self, device):
        """Produces the output shape of the tape by inspecting its measurements
        and the device used for execution.

        .. note::

            The computed shape is not stored because the output shape may be
            dependent on the device used for execution.

        Args:
            device (.Device): the device that will be used for the tape execution

        Raises:
            TapeError: raised for unsupported cases for
                example when the tape contains heterogeneous measurements

        Returns:
            Union[tuple[int], list[tuple[int]]]: the output shape(s) of the
            tape result

        **Example:**

        .. code-block:: python

            dev = qml.device("default.qubit", wires=2)
            a = np.array([0.1, 0.2, 0.3])

            def func(a):
                qml.RY(a[0], wires=0)
                qml.RX(a[1], wires=0)
                qml.RY(a[2], wires=0)

            with qml.tape.QuantumTape() as tape:
                func(a)
                qml.state()

        .. code-block:: pycon

            >>> tape.shape(dev)
            (1, 4)
        """
        output_shape = tuple()

        if len(self._measurements) == 1:
            output_shape = self._single_measurement_shape(self._measurements[0], device)
        else:
            num_measurements = len(set(meas.return_type for meas in self._measurements))
            if num_measurements == 1:
                output_shape = self._multi_homogenous_measurement_shape(self._measurements, device)
            else:
                raise TapeError(
                    "Getting the output shape of a tape that contains multiple types of measurements is unsupported."
                )
        return output_shape

    @property
    def numeric_type(self):
        """Returns the expected numeric type of the tape result by inspecting
        its measurements.

        Raises:
            TapeError: raised for unsupported cases for
                example when the tape contains heterogeneous measurements

        Returns:
            type: the numeric type corresponding to the result type of the
            tape

        **Example:**

        .. code-block:: python

            dev = qml.device("default.qubit", wires=2)
            a = np.array([0.1, 0.2, 0.3])

            def func(a):
                qml.RY(a[0], wires=0)
                qml.RX(a[1], wires=0)
                qml.RY(a[2], wires=0)

            with qml.tape.QuantumTape() as tape:
                func(a)
                qml.state()

        .. code-block:: pycon

            >>> tape.numeric_type
            complex
        """
        measurement_types = set(meas.return_type for meas in self._measurements)
        if len(measurement_types) > 1:
            raise TapeError(
                "Getting the numeric type of a tape that contains multiple types of measurements is unsupported."
            )

        if list(measurement_types)[0] == Sample:

            for observable in self._measurements:
                # Note: if one of the sample measurements contains outputs that
                # are real, then the entire result will be real
                if observable.numeric_type is float:
                    return observable.numeric_type

            return int

        return self._measurements[0].numeric_type

    # INFORMATION CONVERSION ###############################

    @property
    def graph(self):
        """Returns a directed acyclic graph representation of the recorded
        quantum circuit:

        >>> tape.graph
        <pennylane.circuit_graph.CircuitGraph object at 0x7fcc0433a690>

        Note that the circuit graph is only constructed once, on first call to this property,
        and cached for future use.

        Returns:
            .CircuitGraph: the circuit graph object
        """
        if self._graph is None:
            self._graph = qml.CircuitGraph(
                self.operations, self.observables, self.wires, self._par_info, self.trainable_params
            )

        return self._graph

    @property
    def specs(self):
        """Resource information about a quantum circuit.

        Returns:
            dict[str, Union[defaultdict,int]]: dictionaries that contain tape specifications

        **Example**

        .. code-block:: python3

            with qml.tape.QuantumTape() as tape:
                qml.Hadamard(wires=0)
                qml.RZ(0.26, wires=1)
                qml.CNOT(wires=[1, 0])
                qml.Rot(1.8, -2.7, 0.2, wires=0)
                qml.Hadamard(wires=1)
                qml.CNOT(wires=[0, 1])
                qml.expval(qml.PauliZ(0) @ qml.PauliZ(1))

        Asking for the specs produces a dictionary as shown below:

        >>> tape.specs['gate_sizes']
        defaultdict(int, {1: 4, 2: 2})
        >>> tape.specs['gate_types']
        defaultdict(int, {'Hadamard': 2, 'RZ': 1, 'CNOT': 2, 'Rot': 1})

        As ``defaultdict`` objects, any key not present in the dictionary returns 0.

        >>> tape.specs['gate_types']['RX']
        0

        """
        if self._specs is None:
            self._specs = {"gate_sizes": defaultdict(int), "gate_types": defaultdict(int)}

            for op in self.operations:
                # don't use op.num_wires to allow for flexible gate classes like QubitUnitary
                self._specs["gate_sizes"][len(op.wires)] += 1
                self._specs["gate_types"][op.name] += 1

            self._specs["num_operations"] = len(self.operations)
            self._specs["num_observables"] = len(self.observables)
            self._specs["num_diagonalizing_gates"] = len(self.diagonalizing_gates)
            self._specs["num_used_wires"] = self.num_wires
            self._specs["depth"] = self.graph.get_depth()
            self._specs["num_trainable_params"] = self.num_params

        return self._specs

    # pylint: disable=too-many-arguments
    def draw(
        self,
        wire_order=None,
        show_all_wires=False,
        decimals=None,
        max_length=100,
        show_matrices=False,
    ):
        """Draw the quantum tape as a circuit diagram. See :func:`~.drawer.tape_text` for more information.

        Args:
            wire_order (Sequence[Any]): the order (from top to bottom) to print the wires of the circuit
            show_all_wires (bool): If True, all wires, including empty wires, are printed.
            decimals (int): How many decimal points to include when formatting operation parameters.
                Default ``None`` will omit parameters from operation labels.
            max_length (Int) : Maximum length of a individual line.  After this length, the diagram will
                begin anew beneath the previous lines.
            show_matrices=False (bool): show matrix valued parameters below all circuit diagrams

        Returns:
            str: the circuit representation of the tape
        """
        return qml.drawer.tape_text(
            self,
            wire_order=wire_order,
            show_all_wires=show_all_wires,
            decimals=decimals,
            max_length=max_length,
            show_matrices=show_matrices,
        )

    def to_openqasm(self, wires=None, rotations=True, measure_all=True, precision=None):
        # We import decompose_queue here to avoid a circular import
        wires = wires or self.wires

        # add the QASM headers
        qasm_str = "OPENQASM 2.0;\n"
        qasm_str += 'include "qelib1.inc";\n'

        if self.num_wires == 0:
            # empty circuit
            return qasm_str

        # create the quantum and classical registers
        qasm_str += f"qreg q[{len(wires)}];\n"
        qasm_str += f"creg c[{len(wires)}];\n"

        # get the user applied circuit operations
        operations = self.operations

        if rotations:
            # if requested, append diagonalizing gates corresponding
            # to circuit observables
            operations += self.diagonalizing_gates

        temp_circ = Circuit([], self._ops, [])

        # decompose the queue
        # pylint: disable=no-member
        operations = temp_circ.expand(
            depth=2, stop_at=lambda obj: obj.name in OPENQASM_GATES
        ).operations

        # create the QASM code representing the operations
        for op in operations:
            try:
                gate = OPENQASM_GATES[op.name]
            except KeyError as e:
                raise TapeError(f"Operation {op.name} not supported by the QASM serializer") from e

            wire_labels = ",".join([f"q[{wires.index(w)}]" for w in op.wires.tolist()])
            params = ""

            if op.num_params > 0:
                # If the operation takes parameters, construct a string
                # with parameter values.
                if precision is not None:
                    params = "(" + ",".join([f"{p:.{precision}}" for p in op.parameters]) + ")"
                else:
                    # use default precision
                    params = "(" + ",".join([str(p) for p in op.parameters]) + ")"

            qasm_str += f"{gate}{params} {wire_labels};\n"

        # apply computational basis measurements to each quantum register
        # NOTE: This is not strictly necessary, we could inspect self.observables,
        # and then only measure wires which are requested by the user. However,
        # some devices which consume QASM require all registers be measured, so
        # measure all wires by default to be safe.
        if measure_all:
            for wire in range(len(wires)):
                qasm_str += f"measure q[{wire}] -> c[{wire}];\n"
        else:
            measured_wires = qml.wires.Wires.all_wires([m.wires for m in self.measurements])

            for w in measured_wires:
                wire_indx = self.wires.index(w)
                qasm_str += f"measure q[{wire_indx}] -> c[{wire_indx}];\n"

        return qasm_str

    @property
    def hash(self):
        """int: returns an integer hash uniquely representing the quantum tape"""
        fingerprint = []
        fingerprint.extend(op.hash for op in self.operations)
        fingerprint.extend(m.hash for m in self.measurements)
        fingerprint.extend(self.trainable_params)
        return hash(tuple(fingerprint))
