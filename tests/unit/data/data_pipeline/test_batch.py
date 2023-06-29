# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import pytest

from fairseq2.data import read_sequence


class TestBatchOp:
    def test_op_works_as_expected(self) -> None:
        seq = list(range(100))

        batch_size = 4

        dp = read_sequence(seq).batch(batch_size).and_return()

        for _ in range(2):
            it = iter(dp)

            for i in range(25):
                d = next(it)

                offset = i * batch_size

                assert d == [offset + i for i in range(4)]

            with pytest.raises(StopIteration):
                next(it)

            dp.reset()

    def test_op_works_with_batch_size_of_1_as_expected(self) -> None:
        seq = list(range(100))

        dp = read_sequence(seq).batch(1).and_return()

        for _ in range(2):
            it = iter(dp)

            for i in range(100):
                d = next(it)

                assert d == [i]

            with pytest.raises(StopIteration):
                next(it)

            dp.reset()

    def test_op_raises_error_if_batch_size_is_0(self) -> None:
        with pytest.raises(
            ValueError, match=r"^`batch_size` must be greater than zero\.$"
        ):
            read_sequence(list(range(100))).batch(0).and_return()

    @pytest.mark.parametrize("drop", [False, True])
    def test_op_works_with_partial_final_batch_as_expected(self, drop: bool) -> None:
        batch_size = 7

        seq = list(range(100))

        dp = read_sequence(seq).batch(batch_size, drop).and_return()

        for _ in range(2):
            it = iter(dp)

            for i in range(14):
                d = next(it)

                offset = i * batch_size

                assert d == [offset + i for i in range(7)]

            if not drop:
                d = next(it)

                assert d == [98, 99]

            with pytest.raises(StopIteration):
                next(it)

            dp.reset()

    def test_record_reload_position_works_as_expected(self) -> None:
        seq = list(range(1, 10))

        dp = read_sequence(seq).batch(2).and_return()

        d = None

        it = iter(dp)

        # Move the the second example.
        for _ in range(2):
            d = next(it)

        assert d == [3, 4]

        state_dict = dp.state_dict()

        # Read a few examples before we roll back.
        for _ in range(2):
            d = next(it)

        assert d == [7, 8]

        # Expected to roll back to the second example.
        dp.load_state_dict(state_dict)

        # Move to EOD.
        for _ in range(3):
            d = next(it)

        assert d == [9]

        state_dict = dp.state_dict()

        dp.reset()

        # Expected to be EOD.
        dp.load_state_dict(state_dict)

        with pytest.raises(StopIteration):
            next(iter(dp))
