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
"""
Batch transformation for multiple (non-trainable) input examples following issue #2037
"""
from typing import Callable, Sequence, Tuple, Union

import pennylane as qml
from pennylane import numpy as np
from pennylane.tape import QuantumTape
from pennylane.transforms.batch_transform import batch_transform


@batch_transform
def batch_input(
    tape: Union[QuantumTape, qml.QNode],
    argnum: Union[Sequence[int], int],
) -> Tuple[Sequence[QuantumTape], Callable]:
    """
    Transform a QNode to support an initial batch dimension for gate inputs.

    In a classical ML application one needs to batch the non-trainable inputs of the network.
    This function executes the same analogue for a quantum circuit:
    separate circuit executions are created for each input, which are then executed
    with the *same* trainable parameters.

    The batch dimension is assumed to be the first rank of the
    non trainable tensor object. For a rank 1 feature space, the shape needs to be ``(Nt, x)``
    where ``x`` indicates the dimension of the features and ``Nt`` being the number of examples
    within a batch.
    Based on `arXiv:2202.10471 <https://arxiv.org/abs/2202.10471>`__.

    Args:
        tape (.tape.QuantumTape or .QNode): Input quantum circuit to batch
        argnum (Sequence[int] or int): One or more index values indicating the location of the
        quantum operators that use the non-trainable batched parameters.

    Returns:
        Sequence[Sequence[.tape.QuantumTape], Callable]: list of tapes arranged
        according to unbatched inputs and a callable function to batch the results.

    .. seealso:: :func:`~.batch_params`

    **Example**

    .. code-block:: python

        dev = qml.device("default.qubit", wires = 2, shots=None)

        @qml.batch_input(argnum=1)
        @qml.qnode(dev, diff_method="parameter-shift", interface="tf")
        def circuit(inputs, weights):
            qml.RY(weights[0], wires=0)
            qml.AngleEmbedding(inputs, wires = range(2), rotation="Y")
            qml.RY(weights[1], wires=1)
            return qml.expval(qml.PauliZ(1))

    >>> x = tf.random.uniform((10, 2), 0, 1)
    >>> w = tf.random.uniform((2,), 0, 1)
    >>> circuit(x, w)
    <tf.Tensor: shape=(10,), dtype=float64, numpy=
    array([0.46230079, 0.73971315, 0.95666004, 0.5355225 , 0.66180948,
            0.44519553, 0.93874261, 0.9483197 , 0.78737918, 0.90866411])>
    """
    argnum = tuple(argnum) if isinstance(argnum, (list, tuple)) else (int(argnum),)

    trainable_params = tape.get_parameters()
    all_parameters = sum(
        (op.parameters if op.parameters != [] else [None] for op in tape.operations), []
    )
    argnum_params = [all_parameters[i] for i in argnum]

    if any(qml.math.requires_grad(p) for p in argnum_params):
        raise ValueError(
            "Batched inputs must be non-trainable. Please make sure that the parameters indexed by "
            + "'argnum' are not marked as trainable."
        )

    if len(np.unique([qml.math.shape(x)[0] for x in argnum_params])) != 1:
        raise ValueError(
            "Batch dimension for all gate arguments specified by 'argnum' must be the same."
        )

    batch_size = qml.math.shape(argnum_params[0])[0]

    outputs = []
    for i in range(batch_size):
        batch = []
        for idx, param in enumerate(all_parameters):
            if param is None:
                continue
            if idx in argnum:
                param = param[i]
            batch.append(param)
        outputs.append(batch)

    # Construct new output tape with unstacked inputs
    output_tapes = []
    for params in outputs:
        new_tape = tape.copy(copy_operations=True)
        new_tape.set_parameters(params, trainable_only=False)
        output_tapes.append(new_tape)

    return output_tapes, lambda x: qml.math.squeeze(qml.math.stack(x))
