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
# pylint: disable=too-many-arguments

from multimethod import multimethod

import pennylane as qml
from pennylane.operation import Operator, AnyWires
from pennylane.wires import Wires
from pennylane import numpy as np
from ..qubit import PauliY, SWAP, PhaseShift, RX, RY, RZ, Rot

class NoDecompositionShortcut(Exception):
    """decomposition undefined."""

@multimethod
def c1_decomp(op: Operator):
    raise NoDecompositionShortcut


@multimethod
def c1_decomp(op: PauliY, control):
    return [qml.CRY(np.pi, wires=control+op.wires), qml.S(wires=control)]


@multimethod
def c1_decomp(op: SWAP, control):
    return [
            qml.Toffoli(wires=[control, op.wires[1], op.wires[0]]),
            qml.Toffoli(wires=[control, op.wires[0], op.wires[1]]),
            qml.Toffoli(wires=[control, op.wires[1], op.wires[0]]),
    ]


@multimethod
def c1_decomp(op: PhaseShift, control):
    phi = op.data[0]
    return [
            qml.PhaseShift(phi / 2, wires=control),
            qml.CNOT(wires=control+op.wires),
            qml.PhaseShift(-phi / 2, wires=op.wires),
            qml.CNOT(wires=control+op.wires),
            qml.PhaseShift(phi / 2, wires=op.wires),
        ]


@multimethod
def c1_decomp(op: RX, control):
    phi = op.data[0]
    return [
            qml.RZ(np.pi / 2, wires=op.wires),
            qml.RY(phi / 2, wires=op.wires),
            qml.CNOT(wires=control+op.wires),
            qml.RY(-phi / 2, wires=op.wires),
            qml.CNOT(wires=control+op.wires),
            qml.RZ(-np.pi / 2, wires=op.wires),
        ]


@multimethod
def c1_decomp(op: RY, control):
    phi = op.data[0]
    return [
            qml.RY(phi / 2, wires=op.wires),
            qml.CNOT(wires=control+op.wires),
            qml.RY(-phi / 2, wires=op.wires),
            qml.CNOT(wires=control+op.wires),
        ]


@multimethod
def c1_decomp(op: RZ, control):
    phi = op.data[0]
    return [
            qml.PhaseShift(phi / 2, wires=op.wires),
            qml.CNOT(wires=control+op.wires),
            qml.PhaseShift(-phi / 2, wires=op.wires),
            qml.CNOT(wires=control+op.wires),
        ]


@multimethod
def c1_decomp(op: Rot):
    phi, theta, omega = op.data
    wires = op.wires
    return [
            qml.RZ((phi - omega) / 2, wires=wires[1]),
            qml.CNOT(wires=wires),
            qml.RZ(-(phi + omega) / 2, wires=wires[1]),
            qml.RY(-theta / 2, wires=wires[1]),
            qml.CNOT(wires=wires),
            qml.RY(theta / 2, wires=wires[1]),
            qml.RZ(omega, wires=wires[1]),
        ]

class Controlled(Operator):
    
    num_wires = AnyWires

    def __init__(self, base, control_wires, control_values=None, work_wires=None, do_queue=True, id=None):
        
        self.base = base
        self._control_wires = Wires(control_wires)
        self._work_wires = Wires([]) if work_wires is None else Wires(work_wires)
        self._control_values = [1]*len(self._control_wires) if control_values is None else control_values
        self.hyperparameters['control_wires'] = self._control_wires
        self.hyperparameters['control_values'] = self._control_values
        self.hyperparameters['base']  = base
        self.hyperparameters['work_wires'] = self._work_wires
        super().__init__(*base.parameters, wires=(base.wires+self._control_wires+self._work_wires), do_queue=do_queue, id=id)
        self._name = f"C({self.base.name})"

    def queue(self, context=qml.QueuingContext):
        try:
             context.update_info(self.base, owner=self)
        except qml.queuing.QueuingError:
             self.base.queue(context=context)
             context.update_info(self.base, owner=self)

        context.append(self, owns=self.base)

        return self

    @property
    def control_wires(self):
        return self._control_wires

    @property
    def work_wires(self):
        return self._work_wires

    @property
    def target_wires(self):
        return self.base.wires

    @property
    def parameters(self):
        return self.base.parameters
    
    def label(self, decimals=None, base_label=None):
        return self.base.label(decimals=decimals, base_label=base_label)
    
    @staticmethod
    def compute_matrix(*args, base=None, control_wires=None, control_values=None, work_wires=None, **kwargs):
        base_matrix = base.compute_matrix(*args, **kwargs)
        
        base_matrix_size = qml.math.shape(base_matrix)[0]
        num_control_states = 2**len(control_wires)
        total_matrix_size = num_control_states * base_matrix_size

        if control_values is None:
            control_int = 0
        else:
            control_int = sum(2**i * v for i, v in enumerate(reversed(control_values)))

        padding_left = control_int * base_matrix_size
        padding_right = total_matrix_size - base_matrix_size - padding_left

        interface = qml.math.get_interface(base_matrix)
        left_pad = qml.math.cast_like(qml.math.eye(padding_left, like=interface), 1j)
        right_pad = qml.math.cast_like(qml.math.eye(padding_right, like=interface), 1j)
        
        return qml.math.block_diag([left_pad, base_matrix, right_pad])

    @staticmethod
    def compute_decomposition(*args, wires=None, base=None, control_wires=None, control_values=None,
        work_wires=None, **kwargs):

        flips = [qml.PauliX(w) for w, val in zip(control_wires, control_values) if not val]
        if len(control_wires) == 1:
            try:
                return flips+c1_decomp(base, control_wires)+flips
            except NoDecompositionShortcut:
                pass
        return qml.operation.DecompositionUndefinedError

    def generator(self):
        sub_gen = self.base.generator()
        proj_ones = np.ones(len(self.control_wires), requires_grad=False)
        proj = qml.Projector(proj_ones, wires=self.control_wires)
        return (1.0*proj @ sub_gen)

# Non-parametric operations

def CNOT(wires, do_queue=True, id=None):
    return Controlled(qml.PauliX(wires[-1]), wires=wires[0], do_queue=do_queue, id=id)

def Toffoli(wires, do_queue=True, id=None):
    return Controlled(qml.PauliX(wires[-1]), wires[0:-1], do_queue=do_queue, id=id)

def MultiControlledX(wires, control_values=None, work_wires=None, do_queue=True, id=None):
    return Controlled(qml.PauliX(wires[-1]), wires[0:-1], control_values=control_values, work_wires=work_wires,
        do_queue=do_queue, id=id)

def CZ(wires, do_queue=True, id=None):
    return Controlled(qml.PauliZ(wires[-1]), wires=wires[0], do_queue=do_queue, id=id)

def CY(wires, do_queue=True, id=None):
    return Controlled(qml.PauliY(wires[-1]), wires=wires[0], do_queue=do_queue, id=id)

def CSWAP(wires, do_queue=True, id=None):
    return Controlled(qml.SWAP(wires[1:]), wires=wires[0], do_queue=do_queue, id=id)


# parametrix operations

def CRX(phi, wires, do_queue=True, id=None):
    return Controlled(qml.RX(phi, wires=wires[-1]), control_wires=wires[0], do_queue=do_queue, id=id))

def CRY(phi, wires, do_queue=True, id=None):
    return Controlled(qml.RY(phi, wires=wires[-1]), control_wires=wires[0], do_queue=do_queue, id=id))

def CRZ(phi, wires, do_queue=True, id=None):
    return Controlled(qml.RZ(phi, wires=wires[-1]), control_wires=wires[0], do_queue=do_queue, id=id))

def CRot(phi, theta, omega, wires, do_queue=True, id=None):
    return Controlled(qml.Rot(phi, theta, omega, wires=wires[-1]), control_wires=wires[0], do_queue=do_queue, id=id))

def ControlledPhaseShift(phi, wires, do_queue=True, id=None):
    return Controlled(qml.PhaseShift(phi, wires=wires[-1]), control_wires=wires[0], do_queue=do_queue, id=id))

CPhase = ControlledPhaseShift