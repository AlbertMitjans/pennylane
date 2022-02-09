# Copyright 2018-2021 Xanadu Quantum Technologies Inc.

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
A transform to obtain the commutation DAG of a quantum circuit.
"""
import heapq
from functools import wraps
from collections import OrderedDict

import networkx as nx
import pennylane as qml
import pennylane.numpy as np
from pennylane.wires import Wires


def get_dag_commutation(circuit):
    r"""Construct the pairwise-commutation DAG representation of a quantum circuit. A node represents a quantum
    operations and  an edge represent non commutation between two operations. It takes into account that not all
    operations can be moved next to each other by pairwise commutation.

    Args:
        circuit (pennylane.QNode, .QuantumTape, or Callable): A quantum node, tape,
            or function that applies quantum operations.

    Returns:
         function: Function which accepts the same arguments as the QNode or quantum function.
         When called, this function will return the commutation DAG representation of the circuit.

    **Example**

    .. code-block:: python

        def circuit(x, y, z):
            qml.RX(x, wires=0)
            qml.RX(y, wires=0)
            qml.CNOT(wires=[1, 2])
            qml.RY(y, wires=1)
            qml.Hadamard(wires=2)
            qml.CRZ(z, wires=[2, 0])
            qml.RY(-y, wires=1)
            return qml.expval(qml.PauliZ(0))

    The circuit before optimization:

    >>> get_dag = get_dag_commutation(circuit)
    >>> theta = np.pi/4
    >>> get_dag(theta)

    For more details, see:

    * Iten, R., Moyard, R., Metger, T., Sutter, D., Woerner, S.
      "Exact and practical pattern matching for quantum circuit optimization" `arXiv:1909.05270. (2020).
      <https://arxiv.org/pdf/1909.05270.pdf>`_

    """

    # pylint: disable=protected-access

    @wraps(circuit)
    def wrapper(*args, **kwargs):

        if isinstance(circuit, qml.QNode):
            # user passed a QNode, get the tape
            circuit.construct(args, kwargs)
            tape = circuit.qtape

        elif isinstance(circuit, qml.tape.QuantumTape):
            # user passed a tape
            tape = circuit

        elif callable(circuit):
            # user passed something that is callable but not a tape or qnode.
            tape = qml.transforms.make_tape(circuit)(*args, **kwargs)
            # raise exception if it is not a quantum function
            if len(tape.operations) == 0:
                raise ValueError("Function contains no quantum operation")

        else:
            raise ValueError("Input is not a tape, QNode, or quantum function")

        # if no wire ordering is specified, take wire list from tape
        wires = tape.wires

        consecutive_wires = Wires(range(len(wires)))
        wires_map = OrderedDict(zip(wires, consecutive_wires))

        for obs in tape.observables:
            obs._wires = Wires([wires_map[wire] for wire in obs.wires.tolist()])

        with qml.tape.Unwrap(tape):
            # Initialize DAG
            dag = CommutationDAG(consecutive_wires, tape.observables)

            for operation in tape.operations:
                operation._wires = Wires([wires_map[wire] for wire in operation.wires.tolist()])
                dag.add_node(operation)
            dag._add_successors()
        return dag

    return wrapper


position = OrderedDict(
    {
        "Hadamard": 0,
        "PauliX": 1,
        "PauliY": 2,
        "PauliZ": 3,
        "SWAP": 4,
        "ctrl": 5,
        "S": 6,
        "T": 7,
        "SX": 8,
        "ISWAP": 9,
        "SISWAP": 10,
        "Barrier": 11,
        "WireCut": 12,
        "RX": 13,
        "RY": 14,
        "RZ": 15,
        "PhaseShift": 16,
        "Rot": 17,
        "MultiRZ": 18,
        "Identity": 19,
        "U1": 20,
        "U2": 21,
        "U3": 22,
        "IsingXX": 23,
        "IsingYY": 24,
        "IsingZZ": 25,
        "QubitStateVector": 26,
        "BasisState": 27,
    }
)
"""OrderedDict[str, int]: represents the place of each gates in the commutation_map."""

commutation_map = OrderedDict(
    {
        "Hadamard": [
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        ],
        "PauliX": [
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
        ],
        "PauliY": [
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
        ],
        "PauliZ": [
            0,
            0,
            0,
            1,
            0,
            1,
            1,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            1,
            0,
            1,
            1,
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
        ],
        "SWAP": [
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            1,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        ],
        "ctrl": [
            0,
            0,
            0,
            1,
            0,
            1,
            1,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            1,
            0,
            1,
            1,
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
        ],
        "S": [
            0,
            0,
            0,
            1,
            0,
            1,
            1,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            1,
            0,
            1,
            1,
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
        ],
        "T": [
            0,
            0,
            0,
            1,
            0,
            1,
            1,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            1,
            0,
            1,
            1,
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
        ],
        "SX": [
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            1,
            0,
            1,
            1,
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
        ],
        "ISWAP": [
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            1,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        ],
        "SISWAP": [
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            1,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        ],
        "Barrier": [
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        ],
        "WireCut": [
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        ],
        "RX": [
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
        ],
        "RY": [
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
        ],
        "RZ": [
            0,
            0,
            0,
            1,
            0,
            1,
            1,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            1,
            0,
            1,
            1,
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
        ],
        "PhaseShift": [
            0,
            0,
            0,
            1,
            0,
            1,
            1,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            1,
            0,
            1,
            1,
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
        ],
        "Rot": [
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        ],
        "MultiRZ": [
            0,
            0,
            0,
            1,
            0,
            1,
            1,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            1,
            0,
            1,
            1,
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
        ],
        "Identity": [
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            0,
            0,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            0,
            0,
        ],
        "U1": [
            0,
            0,
            0,
            1,
            0,
            1,
            1,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            1,
            0,
            1,
            1,
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
        ],
        "U2": [
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        ],
        "U3": [
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        ],
        "IsingXX": [
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
        ],
        "IsingYY": [
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
        ],
        "IsingZZ": [
            0,
            0,
            0,
            1,
            0,
            1,
            1,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            1,
            0,
            1,
            1,
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
        ],
        "QubitStateVector": [
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        ],
        "BasisState": [
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        ],
    }
)
"""OrderedDict[str, array[bool]]: represents the commutation map of each gates. Positions in the array are the
one defined by the position dictionary. True represents commutation and False non commutation."""


def intersection(wires1, wires2):
    r"""Check if two operations are commuting

    Args:
        wires1 (pennylane.wires.Wires): First set of wires.
        wires2 (pennylane.wires.Wires: Second set of wires.

    Returns:
         bool: True if the two sets of wires are not disjoint and False if disjoint.
    """
    return len(qml.wires.Wires.shared_wires([wires1, wires2])) != 0


def simplify_rotation(rot):
    r"""Simplify a general one qubit rotation into RX, RY and RZ rotation.

    Args:
        rot (pennylane.Rot): One qubit rotation.

    Returns:
         qml.operation: Simplified rotation if possible.
    """

    if np.allclose(np.mod(rot.data[0], 2 * np.pi), np.pi / 2) and np.allclose(
        np.mod(rot.data[2], -2 * np.pi), -np.pi / 2
    ):
        return qml.RX(rot.data[1], wires=rot.wires)
    elif np.allclose(np.mod(rot.data[0], 2 * np.pi), 0) and np.allclose(
        np.mod(rot.data[2], 2 * np.pi), 0
    ):
        return qml.RY(rot.data[1], wires=rot.wires)
    elif np.allclose(np.mod(rot.data[1], 2 * np.pi), 0):
        return qml.RZ(rot.data[0] + rot.data[2], wires=rot.wires)
    elif (
        np.allclose(np.mod(rot.data[0], 2 * np.pi), np.pi)
        and np.allclose(np.mod(rot.data[1], 2 * np.pi), np.pi / 2)
        and np.allclose(np.mod(rot.data[2], 2 * np.pi), 0)
    ):
        return qml.Hadamard(wires=rot.wires)

    return rot


def simplify_controlled_rotation(crot):
    r"""Simplify a general one qubit rotation into RX, RY and RZ rotation.

    Args:
        rot (pennylane.CRot): One qubit controlled rotation.

    Returns:
         qml.operation: Simplified controlled rotation if possible.
    """

    if np.allclose(np.mod(crot.data[0], 2 * np.pi), np.pi / 2) and np.allclose(
        np.mod(crot.data[2], -2 * np.pi), -np.pi / 2
    ):
        return qml.CRX(crot.data[1], wires=crot.wires)
    elif np.allclose(np.mod(crot.data[0], 2 * np.pi), 0) and np.allclose(
        np.mod(crot.data[2], 2 * np.pi), 0
    ):
        return qml.CRY(crot.data[1], wires=crot.wires)
    elif np.allclose(np.mod(crot.data[1], 2 * np.pi), 0):
        return qml.CRZ(crot.data[0] + crot.data[2], wires=crot.wires)
    elif (
        np.allclose(np.mod(crot.data[0], 2 * np.pi), np.pi)
        and np.allclose(np.mod(crot.data[1], 2 * np.pi), np.pi / 2)
        and np.allclose(np.mod(crot.data[2], 2 * np.pi), 0)
    ):
        hadamard = qml.Hadamard
        return qml.ctrl(hadamard, control=crot.control_wires)(wires=crot.target_wires)

    return crot


def simplify_u2(u2):
    r"""Simplify a u2 one qubit rotation into RX and RY rotations.

    Args:
        u2 (pennylane.U2): U2 rotation.

    Returns:
         qml.operation: Simplified rotation if possible.
    """

    if np.allclose(np.mod(u2.data[1], 2 * np.pi), 0) and np.allclose(
        np.mod(u2.data[0] + u2.data[1], 2 * np.pi), 0
    ):
        u2_simplified = qml.RY(np.pi / 2, u2.wires)
    elif np.allclose(np.mod(u2.data[1], np.pi / 2), 0) and np.allclose(
        np.mod(u2.data[0] + u2.data[1], 2 * np.pi), 0
    ):
        u2_simplified = qml.RX(u2.data[1], u2.wires)
    else:
        u2_simplified = u2
    return u2_simplified


def simplify_u3(u3):
    r"""Simplify a general U3 one qubit rotation into RX, RY and RZ rotation.

    Args:
        u3 (pennylane.u3): One qubit U3 rotation.

    Returns:
         qml.operation: Simplified rotation if possible.
    """

    if (
        np.allclose(np.mod(u3.data[2], 2 * np.pi), 0)
        and np.allclose(np.mod(u3.data[1], 2 * np.pi), 0)
        and not np.allclose(np.mod(u3.data[0], 2 * np.pi), 0)
    ):
        u3_simplified = qml.RZ(u3.data[0], u3.wires)
    elif (
        np.allclose(np.mod(u3.data[2], 2 * np.pi), np.pi / 2)
        and np.allclose(np.mod(u3.data[1] + u3.data[1], 2 * np.pi), 0)
        and not np.allclose(np.mod(u3.data[1], 2 * np.pi), 0)
    ):
        u3_simplified = qml.RX(u3.data[1], u3.wires)
    elif (
        np.allclose(np.mod(u3.data[2], 2 * np.pi), 0)
        and not np.allclose(np.mod(u3.data[1], 2 * np.pi), 0)
        and np.allclose(np.mod(u3.data[1] + u3.data[1], 2 * np.pi), 0)
    ):
        u3_simplified = qml.RY(u3.data[0], u3.wires)
    else:
        u3_simplified = u3
    return u3_simplified


def simplify(operation):
    r"""Simplify a rotation into RX, RY and RZ rotations.

    Args:
        operation (pennylane.Operation): One qubit operation.

    Returns:
         qml.operation: Simplified rotation if possible.
    """
    if operation.name == "Rot":
        return simplify_rotation(operation)

    if operation.name == "U2":
        return simplify_u2(operation)

    if operation.name == "U3":
        return simplify_u3(operation)

    if operation.name == "CRot":
        return simplify_controlled_rotation(operation)


def is_commuting(operation1, operation2):
    r"""Check if two operations are commuting. A lookup table is used to check the commutation between the
    controlled, targetted part of operation 1 with the controlled, targetted part of operation 2. It supports
    all PennyLane operations.

    Args:
        operation1 (.Operation): A first quantum operation.
        operation2 (.Operation): A second quantum operation.

    Returns:
         bool: True if the operations commute, False otherwise.

    **Example**

    >>> qml.is_commuting(qml.PauliX(wires=0), qml.PauliZ(wires=0))
    False
    """
    # pylint: disable=too-many-branches
    # pylint: disable=too-many-return-statements

    not_supported_operations = [
        "PauliRot",
        "QubitDensityMatrix",
        "CVNeuralNetLayers",
        "ApproxTimeEvolution",
        "ArbitraryUnitary" "CommutingEvolution",
        "DisplacementEmbedding",
        "SqueezingEmbedding",
    ]

    if (
        operation1.name in not_supported_operations
        or isinstance(operation1, qml.operation.CVOperation)
        or isinstance(operation1, qml.operation.Channel)
    ):
        raise qml.QuantumFunctionError(f"Operation {operation1.name} not supported.")
    if (
        operation2.name in not_supported_operations
        or isinstance(operation2, qml.operation.CVOperation)
        or isinstance(operation2, qml.operation.Channel)
    ):
        raise qml.QuantumFunctionError(f"Operation {operation2.name} not supported.")

    # Simplify the rotations if possible
    if operation1.name in ["U2", "U3", "Rot", "CRot"]:
        operation1 = simplify(operation1)

    if operation2.name in ["U2", "U3", "Rot", "CRot"]:
        operation2 = simplify(operation2)

    # Parametric operation implements identity operator
    if operation1.data and operation1.name != "U2":
        all_zeros = np.allclose(np.mod(operation1.data, 2 * np.pi), 0)
        if all_zeros:
            if operation2.name not in ["Barrier", "WireCut"]:
                return True
            return False

    if operation2.data and operation2.name != "U2":
        all_zeros = np.allclose(np.mod(operation2.data, 2 * np.pi), 0)
        if all_zeros:
            if operation1.name not in ["Barrier", "WireCut"]:
                return True
            return False

    # Case 1 operations are disjoints
    if not intersection(operation1.wires, operation2.wires):
        return True

    non_commuting_operations = [
        "ArbitraryStatePreparation",
        "BasisStatePreparation",
        "MottonenStatePreparation",
        "QubitCarry",
        "QubitSum",
        "SingleExcitation",
        "SingleExcitationMinus",
        "SingleExcitationPlus",
        "DoubleExcitation",
        "DoubleExcitationPlus",
        "DoubleExcitationMinus",
        "BasicEntanglerLayers",
        "GateFabric",
        "ParticleConservingU1",
        "ParticleConservingU2",
        "RandomLayers",
        "SimplifiedTwoDesign",
        "StronglyEntanglingLayers",
        "AllSinglesDoubles",
        "FermionicDoubleExcitation",
        "FermionicSingleExcitation",
        "Grover",
        "kUpCCGSD",
        "Permute",
        "QFT",
        "QuantumMonteCarlo",
        "QuantumPhaseEstimation",
        "UCCSD",
        "MPS",
        "TTN",
        "AmplitudeEmbedding",
        "AngleEmbedding",
        "BasisEmbedding",
        "IQPEmbedding",
        "QAOAEmbedding",
    ]

    if operation1.name or operation2.name in non_commuting_operations:
        return False

    # Two simplified CRot
    if operation1.name == "CRot" and operation2.name == "CRot":
        control_control = intersection(operation1.control_wires, operation2.control_wires)
        target_target = intersection(operation1.target_wires, operation2.target_wires)

        if control_control and target_target:
            return np.all(
                np.allcllose(
                    np.matmul(operation1.matrix, operation2.matrix),
                    np.matmul(operation2.matrix, operation1.matrix),
                )
            )
        elif control_control and not target_target:
            return True
        elif not control_control and target_target:
            return np.all(
                np.allcllose(
                    np.matmul(
                        qml.Rot(*operation1.data, wires=operation1.wires).matrix,
                        qml.Rot(*operation2.data, wires=operation2.wires).matrix,
                    ),
                    np.matmul(
                        qml.Rot(*operation2.data, wires=operation2.wires).matrix,
                        qml.Rot(*operation1.data, wires=operation1.wires).matrix,
                    ),
                )
            )
        return False

    # Two simplified rotations
    if (operation1.name in ["U2", "U3", "Rot", "CRot"]) and (
        operation2.name in ["U2", "U3", "Rot", "CRot"]
    ):
        if operation1.name == "CRot":
            if not intersection(operation1.target_wires, operation2.wires):
                return bool(commutation_map["ctrl"][position[operation2.name]])
            return np.all(
                np.allcllose(
                    np.matmul(
                        qml.Rot(*operation1.data, wires=operation1.target_wires).matrix,
                        operation2.matrix,
                    ),
                    np.matmul(
                        operation2.matrix,
                        qml.Rot(*operation1.data, wires=operation1.target_wires).matrix,
                    ),
                )
            )

        if operation2.name == "CRot":
            if not intersection(operation2.target_wires, operation1.wires):
                return bool(commutation_map[operation1.name][position["ctrl"]])
            return np.all(
                np.allcllose(
                    np.matmul(
                        qml.Rot(*operation2.data, wires=operation2.target_wires).matrix,
                        operation2.matrix,
                    ),
                    np.matmul(
                        operation2.matrix,
                        qml.Rot(*operation2.data, wires=operation2.target_wires).matrix,
                    ),
                )
            )

        return np.all(
            np.allcllose(
                np.matmul(
                    operation1.matrix,
                    operation2.matrix,
                ),
                np.matmul(
                    operation2.matrix,
                    operation1.matrix,
                ),
            )
        )

    # Case 2 both operations are controlled
    if operation1.is_controlled and operation2.is_controlled:
        control_control = intersection(operation1.control_wires, operation2.control_wires)
        target_target = intersection(operation1.target_wires, operation2.target_wires)
        control_target = intersection(operation1.control_wires, operation2.target_wires)
        target_control = intersection(operation1.target_wires, operation2.control_wires)

        # Case 2.1: disjoint targets
        if control_control and not target_target and not control_target and not target_control:
            return True

        # Case 2.2: disjoint controls
        if not control_control and target_target and not control_target and not target_control:
            return bool(
                commutation_map[operation1.is_controlled][position[operation2.is_controlled]]
            )

        # Case 2.3: targets overlap and controls overlap
        if target_target and control_control and not control_target and not target_control:
            return bool(
                commutation_map[operation1.is_controlled][position[operation2.is_controlled]]
            )

        # Case 2.4: targets and controls overlap
        if control_target and target_control and not target_target:
            return bool(commutation_map["ctrl"][position[operation2.is_controlled]]) and bool(
                commutation_map[operation1.is_controlled][position["ctrl"]]
            )

        # Case 2.5: targets overlap with and controls and targets
        if control_target and not target_control and target_target:
            return bool(commutation_map["ctrl"][position[operation2.is_controlled]]) and bool(
                commutation_map[operation1.is_controlled][position[operation2.is_controlled]]
            )

        # Case 2.6: targets overlap with and controls and targets
        if target_control and not control_target and target_target:
            return bool(commutation_map[operation1.is_controlled][position["ctrl"]]) and bool(
                commutation_map[operation1.is_controlled][position[operation2.is_controlled]]
            )

        # Case 2.7: targets overlap with control
        if target_control and not control_target and not target_target:
            return bool(commutation_map[operation1.is_controlled][position["ctrl"]])

        # Case 2.8: targets overlap with control
        if not target_control and control_target and not target_target:
            return bool(commutation_map["ctrl"][position[operation2.is_controlled]])

        # Case 2.9: targets and controls overlap with targets and controls
        if target_control and control_target and target_target:
            return (
                bool(commutation_map[operation1.is_controlled][position["ctrl"]])
                and bool(commutation_map["ctrl"][position[operation2.is_controlled]])
                and bool(
                    commutation_map[operation1.is_controlled][position[operation2.is_controlled]]
                )
            )

    # Case 3: only operation 1 is controlled
    elif operation1.is_controlled:

        control_target = intersection(operation1.control_wires, operation2.wires)
        target_target = intersection(operation1.target_wires, operation2.wires)

        # Case 3.1: control and target 1 overlap with target 2
        if control_target and target_target:
            return bool(
                commutation_map[operation1.is_controlled][position[operation2.name]]
            ) and bool(commutation_map["ctrl"][position[operation2.name]])

        # Case 3.2: control operation 1 overlap with target 2
        if control_target and not target_target:
            return bool(commutation_map["ctrl"][position[operation2.name]])

        # Case 3.3: target 1 overlaps with target 2
        if not control_target and target_target:
            return bool(commutation_map[operation1.is_controlled][position[operation2.name]])

    # Case 4: only operation 2 is controlled
    elif operation2.is_controlled:
        target_control = intersection(operation1.wires, operation2.control_wires)
        target_target = intersection(operation1.wires, operation2.target_wires)

        # Case 4.1: control and target 2 overlap with target 1
        if target_control and target_target:
            return bool(
                commutation_map[operation1.name][position[operation2.is_controlled]]
            ) and bool(commutation_map[operation1.name][position[operation2.is_controlled]])

        # Case 4.2: control operation 2 overlap with target 1
        if target_control and not target_target:
            return bool(commutation_map[operation1.name][position["ctrl"]])

        # Case 4.3: target 1 overlaps with target 2
        if not target_control and target_target:
            return bool(commutation_map[operation1.name][position[operation2.is_controlled]])

    # Case 5: no controlled operations
    # Case 5.1: no controlled operations we simply check the commutation table
    return bool(commutation_map[operation1.name][position[operation2.name]])


def _merge_no_duplicates(*iterables):
    """Merge K list without duplicate using python heapq ordered merging

    Args:
        *iterables: A list of k sorted lists

    Yields:
        Iterator: List from the merging of the k ones (without duplicates
    """
    last = object()
    for val in heapq.merge(*iterables):
        if val != last:
            last = val
            yield val


class CommutationDAGNode:
    r"""Class to store information about a quantum operation in a node of the
    commutation DAG.

    Args:
        op (.Operation): PennyLane operation.
        wires (.Wires): Wires on which the operation acts on.
        node_id (int): ID of the node in the DAG.
        successors (array[int]): List of the node's successors in the DAG.
        predecessors (array[int]): List of the node's predecessors in the DAG.
        reachable (bool): Attribute used to check reachability by pairwise commutation.
        matchedwith (array[int]): ID of the matched node in the pattern.
        isblocked (bool): True for a blocked node, else False.
        successortovisit (array[int]): List of nodes (IDs) to visit in the forward part of the algorithm.
    """

    # pylint: disable=too-many-instance-attributes
    # pylint: disable=too-many-arguments
    # pylint: disable=too-few-public-methods

    __slots__ = [
        "op",
        "wires",
        "target_wires",
        "control_wires",
        "node_id",
        "successors",
        "predecessors",
        "reachable",
        "matchedwith",
        "isblocked",
        "successorstovisit",
    ]

    def __init__(
        self,
        op=None,
        wires=None,
        target_wires=None,
        control_wires=None,
        successors=None,
        predecessors=None,
        reachable=None,
        matchedwith=None,
        successorstovisit=None,
        isblocked=None,
        node_id=-1,
    ):
        self.op = op
        self.wires = wires
        self.target_wires = target_wires
        self.control_wires = control_wires if control_wires is not None else []
        self.node_id = node_id
        self.successors = successors if successors is not None else []
        self.predecessors = predecessors if predecessors is not None else []
        self.reachable = reachable
        self.matchedwith = matchedwith if matchedwith is not None else []
        self.isblocked = isblocked
        self.successorstovisit = successorstovisit if successorstovisit is not None else []


class CommutationDAG:
    r"""Class to represent a quantum circuit as a directed acyclic graph (DAG).

    **Example:**

    **Reference:**

    [1] Iten, R., Moyard, R., Metger, T., Sutter, D. and Woerner, S., 2020.
    Exact and practical pattern matching for quantum circuit optimization.
    `arXiv:1909.05270 <https://arxiv.org/abs/1909.05270>`_

    """

    def __init__(self, wires, observables=None):
        self.wires = wires
        self.num_wires = len(wires)
        self.node_id = -1
        self._multi_graph = nx.MultiDiGraph()
        self.observables = observables if observables is not None else []

    def _add_node(self, node):
        self.node_id += 1
        node.node_id = self.node_id
        self._multi_graph.add_node(node.node_id, node=node)

    def add_node(self, operation):
        """Add the operation as a node in the DAG and updates the edges.

        Args:
            operation (qml.operation): PennyLane quantum operation to add to the DAG.
        """
        if operation.is_controlled:
            new_node = CommutationDAGNode(
                op=operation,
                wires=operation.wires.tolist(),
                target_wires=operation.target_wires.tolist(),
                control_wires=operation.control_wires.tolist(),
                successors=[],
                predecessors=[],
            )
        else:
            new_node = CommutationDAGNode(
                op=operation,
                wires=operation.wires.tolist(),
                target_wires=operation.wires.tolist(),
                successors=[],
                predecessors=[],
            )
        self._add_node(new_node)
        self._update_edges()

    def get_node(self, node_id):
        """Add the operation as a node in the DAG and updates the edges.

        Args:
            node_id (int): PennyLane quantum operation to add to the DAG.

        Returns:
            CommutationDAGNOde: The node with the given id.
        """
        return self._multi_graph.nodes(data="node")[node_id]

    def get_nodes(self):
        """Return iterable to loop through all the nodes in the DAG

        Returns:
            networkx.classes.reportviews.NodeDataView: Iterable nodes.
        """
        return self._multi_graph.nodes(data="node")

    def add_edge(self, node_in, node_out):
        """Add an edge (non commutation) between node_in and node_out.

        Args:
            node_in (int): Id of the ingoing node.
            node_out (int): Id of the outgoing node.

        Returns:
            int: Id of the created edge.
        """
        return self._multi_graph.add_edge(node_in, node_out, commute=False)

    def get_edge(self, node_in, node_out):
        """Get the edge between two nodes if it exists.

        Args:
            node_in (int): Id of the ingoing node.
            node_out (int): Id of the outgoing node.

        Returns:
            dict or None: Default weight is 0, it returns None when there is no edge.
        """
        return self._multi_graph.get_edge_data(node_in, node_out)

    def get_edges(self):
        """Get all edges as an iterable.

        Returns:
            networkx.classes.reportviews.OutMultiEdgeDataView: Iterable over all edges.
        """
        return self._multi_graph.edges.data()

    def _update_edges(self):

        max_node_id = len(self._multi_graph) - 1
        max_node = self.get_node(max_node_id).op

        for current_node_id in range(0, max_node_id):
            self.get_node(current_node_id).reachable = True

        for prev_node_id in range(max_node_id - 1, -1, -1):
            if self.get_node(prev_node_id).reachable and not is_commuting(
                self.get_node(prev_node_id).op, max_node
            ):
                self.add_edge(prev_node_id, max_node_id)
                self._pred_update(max_node_id)
                list_predecessors = self.get_node(max_node_id).predecessors
                for pred_id in list_predecessors:
                    self.get_node(pred_id).reachable = False

    def direct_predecessors(self, node_id):
        """Return the direct predecessors of the given node.

        Args:
            node_id (int): Id of the node in the DAG.

        Returns:
            list[int]: List of the direct predecessors of the given node.
        """
        dir_pred = list(self._multi_graph.pred[node_id].keys())
        dir_pred.sort()
        return dir_pred

    def predecessors(self, node_id):
        """Return the predecessors of the given node.

        Args:
            node_id (int): Id of the node in the DAG.

        Returns:
            list[int]: List of the predecessors of the given node.
        """
        pred = list(nx.ancestors(self._multi_graph, node_id))
        pred.sort()
        return pred

    def direct_successors(self, node_id):
        """Return the direct successors of the given node.

        Args:
            node_id (int): Id of the node in the DAG.

        Returns:
            list[int]: List of the direct successors of the given node.
        """
        dir_succ = list(self._multi_graph.succ[node_id].keys())
        dir_succ.sort()
        return dir_succ

    def successors(self, node_id):
        """Return the successors of the given node.

        Args:
            node_id (int): Id of the node in the DAG.

        Returns:
            list[int]: List of the successors of the given node.
        """
        succ = list(nx.descendants(self._multi_graph, node_id))
        succ.sort()
        return succ

    @property
    def graph(self):
        """Return the DAG object.

        Returns:
            networkx.MultiDiGraph(): Networkx representation of the DAG.
        """
        return self._multi_graph

    def _pred_update(self, node_id):
        self.get_node(node_id).predecessors = []

        for d_pred in self.direct_predecessors(node_id):
            self.get_node(node_id).predecessors.append([d_pred])
            self.get_node(node_id).predecessors.append(self.get_node(d_pred).predecessors)

        self.get_node(node_id).predecessors = list(
            _merge_no_duplicates(*self.get_node(node_id).predecessors)
        )

    def _add_successors(self):

        for node_id in range(len(self._multi_graph) - 1, -1, -1):
            direct_successors = self.direct_successors(node_id)

            for d_succ in direct_successors:
                self.get_node(node_id).successors.append([d_succ])
                self.get_node(node_id).successors.append(self.get_node(d_succ).successors)

            self.get_node(node_id).successors = list(
                _merge_no_duplicates(*self.get_node(node_id).successors)
            )
