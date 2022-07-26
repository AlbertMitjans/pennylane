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
Unit tests for the available qubit state preparation operations.
"""
import itertools as it

import numpy as np
import pytest
from scipy.stats import unitary_group

import pennylane as qml
from pennylane.ops.qubit.attributes import Attribute

# Dummy attribute
new_attribute = Attribute(["PauliX", "PauliY", "PauliZ", "Hadamard", "RZ"])


class TestAttribute:
    """Test addition and inclusion of operations and subclasses in attributes."""

    def test_invalid_input(self):
        """Test that anything that is not a string or Operation throws an error."""
        # Test something that is not an object
        with pytest.raises(TypeError, match="can be checked for attribute inclusion"):
            assert 3 not in new_attribute

        # Test a dummy object that is not an Operation.
        with pytest.raises(TypeError, match="can be checked for attribute inclusion"):
            assert object() not in new_attribute

    def test_string_inclusion(self):
        """Test that we can check inclusion using strings."""
        assert "PauliX" in new_attribute
        assert "RX" not in new_attribute

    def test_operation_class_inclusion(self):
        """Test that we can check inclusion using Operations."""
        assert qml.PauliZ(0) in new_attribute
        assert qml.RX(0.5, wires=0) not in new_attribute

    def test_operation_subclass_inclusion(self):
        """Test that we can check inclusion using subclasses of Operations, whether
        or not anything has been instantiated."""
        assert qml.RZ in new_attribute
        assert qml.RX not in new_attribute

    def test_invalid_addition(self):
        """Test that an error is raised if we try to add something
        other than an Operation or a string."""
        with pytest.raises(TypeError, match="can be added to an attribute"):
            new_attribute.add(0)

        assert len(new_attribute) == 5

        with pytest.raises(TypeError, match="can be added to an attribute"):
            new_attribute.add(object())

        assert len(new_attribute) == 5

    def test_inclusion_after_addition(self):
        """Test that we can add operators to the set in multiple ways."""
        attribute = Attribute(["PauliX", "PauliY", "PauliZ", "Hadamard", "RZ"])
        attribute.add("RX")
        attribute.add(qml.PhaseShift(0.5, wires=0))
        attribute.add(qml.RY)

        assert "RX" in attribute
        assert "PhaseShift" in attribute
        assert "RY" in attribute
        assert len(attribute) == 8

    def test_tensor_check(self):
        """Test that we can ask if a tensor is in the attribute."""
        assert qml.PauliX(wires=0) @ qml.PauliZ(wires=1) not in new_attribute


single_scalar_single_wire_ops = [
    "RX",
    "RY",
    "RZ",
    "PhaseShift",
    "U1",
]

single_scalar_multi_wire_ops = [
    "ControlledPhaseShift",
    "CRX",
    "CRY",
    "CRZ",
    "IsingXX",
    "IsingYY",
    "IsingZZ",
]

two_scalar_single_wire_ops = [
    "U2",
]

three_scalar_single_wire_ops = [
    "Rot",
    "U3",
]

three_scalar_multi_wire_ops = [
    "CRot",
]

separately_tested_ops = [
    "QubitUnitary",
    "ControlledQubitUnitary",
    "DiagonalQubitUnitary",
    "PauliRot",
    "MultiRZ",
]


class TestSupportsBroadcasting:
    """Test that all operations in the ``supports_broadcasting`` attribute
    actually support broadcasting."""

    def test_all_marked_operations_are_tested(self):
        """Test that the subsets of the ``supports_broadcasting`` attribute
        defined above cover the entire attribute."""
        tested_ops = set(
            it.chain.from_iterable(
                [
                    single_scalar_single_wire_ops,
                    single_scalar_multi_wire_ops,
                    two_scalar_single_wire_ops,
                    three_scalar_single_wire_ops,
                    three_scalar_multi_wire_ops,
                    separately_tested_ops,
                ]
            )
        )

        assert tested_ops == qml.ops.qubit.attributes.supports_broadcasting

    @pytest.mark.parametrize("name", single_scalar_single_wire_ops)
    def test_single_scalar_single_wire_ops(self, name):
        """Test that single-scalar-parameter operations on a single wire marked
        as supporting parameter broadcasting actually do support broadcasting."""
        par = np.array([0.25, 2.1, -0.42])
        wires = ["wire0"]

        cls = getattr(qml, name)
        op = cls(par, wires=wires)

        mat1 = op.matrix()
        mat2 = cls.compute_matrix(par)
        single_mats = [cls(p, wires=wires).matrix() for p in par]

        assert qml.math.allclose(mat1, single_mats)
        assert qml.math.allclose(mat2, single_mats)

    @pytest.mark.parametrize("name", single_scalar_multi_wire_ops)
    def test_single_scalar_multi_wire_ops(self, name):
        """Test that single-scalar-parameter operations on multiple wires marked
        as supporting parameter broadcasting actually do support broadcasting."""
        par = np.array([0.25, 2.1, -0.42])
        wires = ["wire0", 5]

        cls = getattr(qml, name)
        op = cls(par, wires=wires)

        mat1 = op.matrix()
        mat2 = cls.compute_matrix(par)
        single_mats = [cls(p, wires=wires).matrix() for p in par]

        assert qml.math.allclose(mat1, single_mats)
        assert qml.math.allclose(mat2, single_mats)

    @pytest.mark.parametrize("name", two_scalar_single_wire_ops)
    def test_two_scalar_single_wire_ops(self, name):
        """Test that two-scalar-parameter operations on a single wire marked
        as supporting parameter broadcasting actually do support broadcasting."""
        par = (np.array([0.25, 2.1, -0.42]), np.array([-6.2, 0.12, 0.421]))
        wires = ["wire0"]

        cls = getattr(qml, name)
        op = cls(*par, wires=wires)

        mat1 = op.matrix()
        mat2 = cls.compute_matrix(*par)
        single_pars = [tuple(p[i] for p in par) for i in range(3)]
        single_mats = [cls(*p, wires=wires).matrix() for p in single_pars]

        assert qml.math.allclose(mat1, single_mats)
        assert qml.math.allclose(mat2, single_mats)

    @pytest.mark.parametrize("name", three_scalar_single_wire_ops)
    def test_three_scalar_single_wire_ops(self, name):
        """Test that three-scalar-parameter operations on a single wire marked
        as supporting parameter broadcasting actually do support broadcasting."""
        par = (
            np.array([0.25, 2.1, -0.42]),
            np.array([-6.2, 0.12, 0.421]),
            np.array([0.2, 1.1, -5.2]),
        )
        wires = ["wire0"]

        cls = getattr(qml, name)
        op = cls(*par, wires=wires)

        mat1 = op.matrix()
        mat2 = cls.compute_matrix(*par)
        single_pars = [tuple(p[i] for p in par) for i in range(3)]
        single_mats = [cls(*p, wires=wires).matrix() for p in single_pars]

        assert qml.math.allclose(mat1, single_mats)
        assert qml.math.allclose(mat2, single_mats)

    @pytest.mark.parametrize("name", three_scalar_multi_wire_ops)
    def test_three_scalar_multi_wire_ops(self, name):
        """Test that three-scalar-parameter operations on multiple wires marked
        as supporting parameter broadcasting actually do support broadcasting."""
        par = (
            np.array([0.25, 2.1, -0.42]),
            np.array([-6.2, 0.12, 0.421]),
            np.array([0.2, 1.1, -5.2]),
        )
        wires = ["wire0", 214]

        cls = getattr(qml, name)
        op = cls(*par, wires=wires)

        mat1 = op.matrix()
        mat2 = cls.compute_matrix(*par)
        single_pars = [tuple(p[i] for p in par) for i in range(3)]
        single_mats = [cls(*p, wires=wires).matrix() for p in single_pars]

        assert qml.math.allclose(mat1, single_mats)
        assert qml.math.allclose(mat2, single_mats)

    def test_qubit_unitary(self):
        """Test that QubitUnitary, which is marked as supporting parameter broadcasting,
        actually does support broadcasting."""

        U = np.array([unitary_group.rvs(4, random_state=state) for state in [91, 1, 4]])
        wires = [0, "9"]

        op = qml.QubitUnitary(U, wires=wires)

        mat1 = op.matrix()
        mat2 = qml.QubitUnitary.compute_matrix(U)
        single_mats = [qml.QubitUnitary(_U, wires=wires).matrix() for _U in U]

        assert qml.math.allclose(mat1, single_mats)
        assert qml.math.allclose(mat2, single_mats)

    def test_controlled_qubit_unitary(self):
        """Test that ControlledQubitUnitary, which is marked as supporting parameter broadcasting,
        actually does support broadcasting."""

        U = np.array([unitary_group.rvs(4, random_state=state) for state in [91, 1, 4]])
        wires = [0, "9"]

        op = qml.ControlledQubitUnitary(U, wires=wires, control_wires=[1, "10"])

        mat1 = op.matrix()
        mat2 = qml.ControlledQubitUnitary.compute_matrix(U, u_wires=wires, control_wires=[1, "10"])
        single_mats = [
            qml.ControlledQubitUnitary(_U, wires=wires, control_wires=[1, "10"]).matrix()
            for _U in U
        ]

        assert qml.math.allclose(mat1, single_mats)
        assert qml.math.allclose(mat2, single_mats)

    def test_diagonal_qubit_unitary(self):
        """Test that DiagonalQubitUnitary, which is marked as supporting parameter broadcasting,
        actually does support broadcasting."""
        diag = np.array([[1j, 1, 1, -1j], [-1j, 1j, 1, -1], [1j, -1j, 1.0, -1]])
        wires = ["a", 5]

        op = qml.DiagonalQubitUnitary(diag, wires=wires)

        mat1 = op.matrix()
        mat2 = qml.DiagonalQubitUnitary.compute_matrix(diag)
        single_mats = [qml.DiagonalQubitUnitary(d, wires=wires).matrix() for d in diag]

        assert qml.math.allclose(mat1, single_mats)
        assert qml.math.allclose(mat2, single_mats)

    @pytest.mark.parametrize(
        "pauli_word, wires", [("XYZ", [0, "4", 1]), ("II", [1, 5]), ("X", [7])]
    )
    def test_pauli_rot(self, pauli_word, wires):
        """Test that PauliRot, which is marked as supporting parameter broadcasting,
        actually does support broadcasting."""
        par = np.array([0.25, 2.1, -0.42])

        op = qml.PauliRot(par, pauli_word, wires=wires)

        mat1 = op.matrix()
        mat2 = qml.PauliRot.compute_matrix(par, pauli_word=pauli_word)
        single_mats = [qml.PauliRot(p, pauli_word, wires=wires).matrix() for p in par]

        assert qml.math.allclose(mat1, single_mats)
        assert qml.math.allclose(mat2, single_mats)

    @pytest.mark.parametrize("wires", [[0, "4", 1], [1, 5], [7]])
    def test_pauli_rot(self, wires):
        """Test that MultiRZ, which is marked as supporting parameter broadcasting,
        actually does support broadcasting."""
        par = np.array([0.25, 2.1, -0.42])

        op = qml.MultiRZ(par, wires=wires)

        mat1 = op.matrix()
        mat2 = qml.MultiRZ.compute_matrix(par, num_wires=len(wires))
        single_mats = [qml.MultiRZ(p, wires=wires).matrix() for p in par]

        assert qml.math.allclose(mat1, single_mats)
        assert qml.math.allclose(mat2, single_mats)
