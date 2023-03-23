import pytest
import torch

from fairseq2.data.text import VocabularyInfo
from fairseq2.generate import search
from tests.common import assert_close, assert_equal, device, has_no_inf, has_no_nan


def test_log_prob() -> None:
    assert_close(
        search.dec_out_to_log_prob(
            torch.tensor(
                [
                    [0.0, 0.0, 0.0, 1.0],
                    [0.0, 0.0, 1.0, torch.nan],
                    [0.0, 0.0, 1.0, torch.inf],
                    [0.0, 0.0, 1.0, -torch.inf],
                ],
                device=device,
            ),
            temperature=0.0,
            pad=0,
            bos=1,
        ),
        [
            [-torch.inf, -torch.inf, -1.743668, -0.743668],
            [-torch.inf, -torch.inf, -torch.inf, -torch.inf],
            [-torch.inf, -torch.inf, -torch.inf, -torch.inf],
            [-torch.inf, -torch.inf, -0.551445, -torch.inf],
        ],
    )


def test_force_token() -> None:
    t = torch.tensor([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], device=device)
    search.force_token_(t, token=1)

    assert_equal(t, [[-torch.inf, 2.0, -torch.inf], [-torch.inf, 5.0, -torch.inf]])


def test_prepare_state_noprefix() -> None:
    vocab_info = VocabularyInfo(size=8, bos_idx=0, eos_idx=1, unk_idx=2, pad_idx=3)
    bs = search.BeamSearchStrategy(vocab_info=vocab_info, max_len=100, beam_size=3)
    src_tokens = torch.tensor(
        [[1, 2, 3, 4], [7, 8, 9, 10]], dtype=torch.int64, device=device
    )

    # min(100, 2 * 4 + 10) -> 18
    exp_max_len = 18
    # (bsz:2, beam_size:3, (exp_max_len:18 + 2))
    # (2, 3, 20)
    expected_tokens = torch.full((2, 3, 20), vocab_info.pad_idx, device=device)
    expected_tokens[:, :, 0] = vocab_info.bos_idx

    s = bs.new_search_job(src_tokens)

    assert s.step == 0
    assert s.n_prefix_tokens == 1
    assert s.max_len == exp_max_len
    assert_equal(s.tokens, expected_tokens)
    assert_equal(s.scores, torch.zeros((2, 3, 20), device=device))
    assert_equal(
        s.finished_mask,  # (bsz, beam_size)
        torch.tensor([[False, False, False], [False, False, False]], device=device),
    )


def test_prepare_state_noprefix_maxlen() -> None:
    vocab_info = VocabularyInfo(size=8, bos_idx=0, eos_idx=1, unk_idx=2, pad_idx=3)

    bs = search.BeamSearchStrategy(vocab_info=vocab_info, max_len=10, beam_size=1)

    src_tokens = torch.tensor(
        [[1, 2, 3, 4], [7, 8, 9, 10]], dtype=torch.int64, device=device
    )

    # min(10, 2 * 4 + 10) -> 10
    exp_max_len = 10

    # (bsz:2, beam_size:1, (exp_max_len:10 + 2))
    # (2, 12)
    expected_tokens = torch.full((2, 1, 12), vocab_info.pad_idx, device=device)
    expected_tokens[:, :, 0] = vocab_info.bos_idx

    s = bs.new_search_job(src_tokens)

    assert s.n_prefix_tokens == 1
    assert s.max_len == exp_max_len
    assert_equal(s.tokens, expected_tokens)


def test_prepare_state_prefix_single() -> None:
    vocab_info = VocabularyInfo(size=8, bos_idx=0, eos_idx=1, unk_idx=2, pad_idx=3)

    bs = search.BeamSearchStrategy(vocab_info=vocab_info, max_len=10, beam_size=2)
    src_tokens = torch.tensor(
        [[1, 2, 3, 4], [7, 8, 9, 10]], dtype=torch.int64, device=device
    )
    prefix_tokens = torch.tensor([99, 17], dtype=torch.int64, device=device)

    # min(100, 2 * 4 + 10) -> 18
    exp_max_len = 10

    P = vocab_info.pad_idx
    expected_tokens = torch.tensor(
        [
            [
                [99, 17, P, P, P, P, P, P, P, P, P, P],
                [99, 17, P, P, P, P, P, P, P, P, P, P],
            ],
            [
                [99, 17, P, P, P, P, P, P, P, P, P, P],
                [99, 17, P, P, P, P, P, P, P, P, P, P],
            ],
        ],
        device=device,
    )

    state = bs.new_search_job(src_tokens=src_tokens, prefix_tokens=prefix_tokens)

    assert state.step == 0
    assert state.n_prefix_tokens == 2
    assert state.max_len == exp_max_len

    assert_equal(state.tokens, expected_tokens)
    assert_equal(state.scores, torch.zeros((2, 2, 10 + 2)))
    assert_equal(
        state.finished_mask,  # (bsz, beam_size)
        torch.tensor([[False, False], [False, False]], device=device),
    )


def test_prepare_state_prefix_batched() -> None:
    vocab_info = VocabularyInfo(size=8, bos_idx=0, eos_idx=1, unk_idx=2, pad_idx=3)

    bs = search.BeamSearchStrategy(vocab_info=vocab_info, max_len=10, beam_size=2)
    src_tokens = torch.tensor([[1, 2, 3, 4], [7, 8, 9, 10]], dtype=torch.int64)
    prefix_tokens = torch.tensor([[99, 17], [88, 18]], dtype=torch.int64)

    # min(100, 2 * 4 + 10) -> 18
    exp_max_len = 10
    P = vocab_info.pad_idx
    expected_tokens = torch.tensor(
        [
            [
                [99, 17, P, P, P, P, P, P, P, P, P, P],
                [99, 17, P, P, P, P, P, P, P, P, P, P],
            ],
            [
                [88, 18, P, P, P, P, P, P, P, P, P, P],
                [88, 18, P, P, P, P, P, P, P, P, P, P],
            ],
        ]
    )

    s = bs.new_search_job(src_tokens=src_tokens, prefix_tokens=prefix_tokens)

    assert s.n_prefix_tokens == 2
    assert s.max_len == exp_max_len
    assert_equal(s.tokens, expected_tokens)
    assert_equal(s.scores, torch.zeros((2, 2, 10 + 2)))
    assert_equal(
        s.finished_mask,  # (bsz, beam_size)
        torch.tensor([[False, False], [False, False]]),
    )


def test_step_done() -> None:
    vocab_info = VocabularyInfo(size=8, bos_idx=0, eos_idx=1, unk_idx=2, pad_idx=3)

    bs = search.BeamSearchStrategy(vocab_info=vocab_info, max_len=10, beam_size=1)

    src_tokens = torch.tensor([[1, 2, 3, 4], [7, 8, 9, 10]], dtype=torch.int64)

    s = bs.new_search_job(src_tokens)
    assert s.step == 0
    assert s.n_prefix_tokens == 1
    assert s.batch_size == 2
    assert s.beam_size == 1

    dec_out = torch.rand((s.flat_size, vocab_info.size))
    s.done = True
    with pytest.raises(AssertionError, match="done == True"):
        s.update(dec_out)


def test_step_bad_dec_shape() -> None:
    vocab_info = VocabularyInfo(size=8, unk_idx=0, bos_idx=1, eos_idx=2, pad_idx=3)
    bs = search.BeamSearchStrategy(vocab_info=vocab_info, max_len=10, beam_size=2)

    src_tokens = torch.tensor([[1, 2, 3, 4], [7, 8, 9, 10]], dtype=torch.int64)

    s = bs.new_search_job(src_tokens)
    assert s.n_prefix_tokens == 1
    assert s.batch_size == 2
    assert s.beam_size == 2

    dec_out = torch.rand((s.flat_size * 2, vocab_info.size + 1))

    with pytest.raises(AssertionError, match="input_beam_size .* must == .* beam_size"):
        s.update(dec_out)


def test_step_one() -> None:
    vocab_info = VocabularyInfo(size=8, unk_idx=0, bos_idx=1, eos_idx=2, pad_idx=3)
    beam_size = 2

    bs = search.BeamSearchStrategy(
        vocab_info=vocab_info, max_len=10, beam_size=beam_size
    )

    batch_size = 2
    src_len = 4

    src_tokens = torch.zeros(size=(batch_size, src_len), dtype=torch.int64)

    s = bs.new_search_job(src_tokens)
    assert s.step == 0
    assert s.n_prefix_tokens == 1
    assert s.batch_size == 2
    assert s.beam_size == 2

    assert (s.tokens[:, :, 0] == vocab_info.bos_idx).all()
    assert (s.tokens[:, :, 1:] == vocab_info.pad_idx).all()
    assert (s.scores == 0.0).all()

    dec_out_beam = torch.tensor(
        [
            # batch
            [
                # beam
                [
                    # [ UNK, BOS, EOS, PAD, ... ]
                    [0.0, 0.0, 0.0, 0.0, 0.3, 5.0, 0.0, 0.0],
                    [0.0, 0.0, 0.0, 0.0, 0.3, 5.0, 0.0, 0.0],
                ]
            ],
            [
                [
                    [0.0, 0.0, 0.0, 0.0, 5.0, 0.0, 2.0, 0.0],
                    [0.0, 0.0, 0.0, 0.0, 5.0, 0.0, 2.0, 0.0],
                ]
            ],
        ]
    )
    # (bsz * beam_size, vocab)
    dec_out = dec_out_beam.view(-1, vocab_info.size)

    s.update(dec_out)
    assert s.step == 1

    dec_out_log_prob = s._log_prob(dec_out, step=s.step, max_len=s.max_len)

    dec_out_log_prob_beam = dec_out_log_prob.view(
        batch_size, beam_size, vocab_info.size
    )

    assert_equal(s.tokens[:, :, 1], [[5, 4], [4, 6]])
    assert_equal(
        s.scores[:, :, 1],
        [
            [dec_out_log_prob_beam[0, 1, 5], dec_out_log_prob_beam[0, 0, 4]],
            [dec_out_log_prob_beam[1, 1, 4], dec_out_log_prob_beam[1, 0, 6]],
        ],
    )


def test_step_continue() -> None:
    vocab_info = VocabularyInfo(size=8, unk_idx=0, bos_idx=1, eos_idx=2, pad_idx=3)

    beam_size = 2
    batch_size = 2
    src_len = 4
    bs = search.BeamSearchStrategy(
        vocab_info=vocab_info, max_len=10, beam_size=beam_size
    )

    src_tokens = torch.zeros(size=(batch_size, src_len), dtype=torch.int64)
    s = bs.new_search_job(src_tokens)
    assert s.step == 0
    assert s.n_prefix_tokens == 1
    assert s.batch_size == 2
    assert s.beam_size == 2

    s.step = 1
    s.scores[:, :, 1] = torch.tensor([[[0.5, 0.0], [0.0, 0.05]]])
    # > 3
    s.tokens[:, :, 1] = torch.tensor([[[4, 5], [6, 7]]])

    dec_out_beam = torch.tensor(
        [
            # batch
            [
                # beam
                # [ UNK, BOS, EOS, PAD, ... ]
                [0.0, 0.0, 0.0, 0.0, 0.3, 0.0, 0.0, 0.0],
                [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 7.0],
            ],
            [
                [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 15.0],
                [0.0, 0.0, 0.0, 0.0, 5.0, 0.0, 0.0, 0.0],
            ],
        ]
    )
    # (bsz * beam_size, vocab)
    dec_out = dec_out_beam.view(-1, vocab_info.size)

    s.update(dec_out)
    assert s.step == 2

    dec_out_log_prob = s._log_prob(dec_out, step=s.step, max_len=s.max_len)

    dec_out_log_prob_beam = dec_out_log_prob.view(
        batch_size, beam_size, vocab_info.size
    )

    assert_equal(s.finished_mask, [[False, False], [False, False]])

    # in selecting beams, we restructure history:
    assert_equal(s.tokens[:, :, s.step - 1], [[5, 4], [7, 6]])
    assert_equal(s.tokens[:, :, s.step], [[7, 4], [4, 7]])
    assert_equal(
        s.scores[:, :, s.step],
        [
            [
                s.scores[0, 0, s.step - 1] + dec_out_log_prob_beam[0, 1, 7],
                s.scores[0, 1, s.step - 1] + dec_out_log_prob_beam[0, 0, 4],
            ],
            [
                s.scores[1, 0, s.step - 1] + dec_out_log_prob_beam[1, 1, 4],
                s.scores[1, 1, s.step - 1] + dec_out_log_prob_beam[1, 0, 7],
            ],
        ],
    )


def test_step_finished() -> None:
    vocab_info = VocabularyInfo(size=8, unk_idx=0, bos_idx=1, eos_idx=2, pad_idx=3)

    beam_size = 2
    batch_size = 2
    src_len = 4

    bs = search.BeamSearchStrategy(
        vocab_info=vocab_info,  # force min_len == 0
        min_len=1,
        max_len=10,
        beam_size=beam_size,
    )

    src_tokens = torch.zeros(size=(batch_size, src_len), dtype=torch.int64)
    s = bs.new_search_job(src_tokens)
    assert s.step == 0
    assert s.n_prefix_tokens == 1
    assert s.batch_size == 2
    assert s.beam_size == 2

    assert (s.tokens[:, :, 0] == vocab_info.bos_idx).all()
    assert (s.tokens[:, :, 1:] == vocab_info.pad_idx).all()
    assert (s.scores == 0.0).all()

    dec_out_beam = torch.tensor(
        [
            # batch
            [
                # beam
                # [ UNK, BOS, EOS, PAD, ... ]
                [0.0, 0.0, 0.0, 0.0, 0.3, 5.0, 0.0, 0.0],
                [0.0, 0.0, 0.0, 0.0, 0.3, 5.0, 0.0, 0.0],
            ],
            [
                # force EOS here
                [0.0, 0.0, 20.0, 0.0, 10.0, 0.0, 0.0, 0.0],
                [0.0, 0.0, 20.0, 0.0, 10.0, 0.0, 0.0, 0.0],
            ],
        ]
    )
    # (bsz * beam_size * search_breadth, vocab)
    dec_out = dec_out_beam.view(-1, vocab_info.size)

    s.update(dec_out)
    assert s.step == 1

    dec_out_log_prob = s._log_prob(dec_out, step=s.step, max_len=s.max_len)
    dec_out_log_prob_beam = dec_out_log_prob.view(
        batch_size, beam_size, vocab_info.size
    )

    assert_close(s.finished_mask, [[False, False], [True, False]])
    assert_close(s.tokens[:, :, s.step], [[5, 4], [vocab_info.eos_idx, 4]])
    assert_close(
        s.scores[:, :, s.step],
        [
            [dec_out_log_prob_beam[0, 1, 5], dec_out_log_prob_beam[0, 0, 4]],
            [
                dec_out_log_prob_beam[1, 0, vocab_info.eos_idx],
                dec_out_log_prob_beam[1, 1, 4],
            ],
        ],
    )

    dec_out_beam = torch.tensor(
        [
            # batch
            [
                # beam
                # [ UNK, BOS, EOS, PAD, ... ]
                [0.0, 0.0, 0.0, 0.0, 0.3, 0.0, 0.0, 0.0],
                [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.2],
            ],
            [
                # should be masked by previous eos_idx
                [0.0, 0.0, 0.0, 0.0, 3.0, 0.0, 0.0, 0.0],
                [0.0, 0.0, 0.0, 0.0, 10.0, 0.0, 0.0, 0.0],
            ],
        ]
    )
    # (bsz * beam_size * search_breadth, vocab)
    dec_out = dec_out_beam.view(-1, vocab_info.size)

    s.update(dec_out)
    assert s.step == 2

    # finished (but still selected) beams have token pad_idx
    assert_equal(s.tokens[:, :, s.step], [[4, 5], [vocab_info.pad_idx, 4]])

    # finished (but still selected) beams have score[step] == score[step-1]
    assert s.scores[1, 0, s.step] == s.scores[1, 0, s.step - 1]


def test_finalize_notop() -> None:
    vocab_info = VocabularyInfo(size=8, unk_idx=0, bos_idx=1, eos_idx=2, pad_idx=3)
    bs = search.BeamSearchStrategy(vocab_info=vocab_info, max_len=10, beam_size=3)

    src_tokens = torch.tensor([[1, 2, 3, 4], [7, 8, 9, 10]], dtype=torch.int64)

    # ((bsz:2 * beam_size:3), (exp_max_len:10 + 2))
    # (6, 12)

    s = bs.new_search_job(src_tokens)

    # beam_size = 3
    assert s.tokens.shape == (2, 3, 12)
    s.tokens = torch.randint_like(s.tokens, low=0, high=1000)
    s.scores = torch.rand_like(s.scores)

    sr = s.finalize()
    assert_equal(sr.tokens.view(2, 3, -1), s.tokens)
    assert_equal(sr.scores.view(2, 3, -1), s.scores)


def test_finalize_top() -> None:
    vocab_info = VocabularyInfo(size=8, unk_idx=0, bos_idx=1, eos_idx=2, pad_idx=3)
    bs = search.BeamSearchStrategy(vocab_info=vocab_info, max_len=10, beam_size=3)

    src_tokens = torch.tensor([[1, 2, 3, 4], [7, 8, 9, 10]], dtype=torch.int64)

    s = bs.new_search_job(src_tokens)
    s.step = 5

    # ((bsz:2 * beam_size:3), (exp_max_len:10 + 2))
    assert s.tokens.shape == (2, 3, 12)
    s.tokens = torch.randint_like(s.tokens, low=0, high=1000)
    s.scores = torch.rand_like(s.scores)

    s.tokens[:, :, s.step + 1 :] = vocab_info.pad_idx
    s.scores[:, :, s.step + 1 :] = -torch.inf

    # Force scores at step with a known sort order.
    # top-k [[1, 2], [1, 0]]
    s.scores[:, :, s.step] = torch.tensor([[0.1, 0.9, 0.3], [0.4, 0.7, 0.2]])

    sr = s.finalize(top=2)

    assert_equal(
        sr.scores,
        torch.stack(
            [
                torch.stack([s.scores[0, 1, :], s.scores[0, 2, :]]),
                torch.stack([s.scores[1, 1, :], s.scores[1, 0, :]]),
            ]
        ),
    )
    assert_equal(
        sr.tokens,
        torch.stack(
            [
                torch.stack([s.tokens[0, 1, :], s.tokens[0, 2, :]]),
                torch.stack([s.tokens[1, 1, :], s.tokens[1, 0, :]]),
            ]
        ),
    )


def test_choose_beams() -> None:
    vocab_info = VocabularyInfo(size=8, unk_idx=0, bos_idx=1, eos_idx=2, pad_idx=3)
    bs = search.BeamSearchStrategy(
        vocab_info=vocab_info, min_len=10, max_len=20, beam_size=2
    )

    src_tokens = torch.tensor([[1, 2, 3, 4], [7, 8, 9, 10]], dtype=torch.int64)
    s = bs.new_search_job(src_tokens)

    # (bsz=2, input_beam_size=2, size=4)
    ps = torch.tensor(
        [
            [[4.0, 0.0, 0.0, 0.0], [0.0, 0.0, 3.0, 0.0]],
            [[0.5, 4.5, 0.5, 1.5], [0.5, 0.5, 0.5, 0.75]],
        ]
    )

    # Manually increase the step since we manually generated the probs
    s.step += 2
    sel = s._choose_beams(ps)
    assert_equal(sel.scores, [[4.0, 3.0], [4.5, 1.5]])
    assert_equal(sel.tokens, [[0, 2], [1, 3]])
    assert_equal(sel.beams, [[0, 1], [0, 0]])


def test_log_prob_below_min() -> None:
    vocab_info = VocabularyInfo(size=8, unk_idx=0, bos_idx=1, eos_idx=2, pad_idx=3)

    max_len = 20
    bs = search.BeamSearchStrategy(vocab_info=vocab_info, min_len=10, max_len=max_len)

    src_tokens = torch.tensor([[1, 2, 3, 4], [7, 8, 9, 10]], dtype=torch.int64)

    s = bs.new_search_job(src_tokens)

    # anything < size, not in the tokens.
    a_idx = 5

    t = torch.rand((2, vocab_info.size))

    step = 1
    lprobs = s._log_prob(t, step=step, max_len=max_len)

    raw = search.dec_out_to_log_prob(
        t, temperature=0.1, pad=vocab_info.pad_idx, bos=vocab_info.bos_idx
    )
    assert_equal(
        lprobs[:, vocab_info.unk_idx], (raw[:, vocab_info.unk_idx] - bs.unk_penalty)
    )
    assert_equal(lprobs[:, vocab_info.pad_idx], torch.tensor([-torch.inf, -torch.inf]))

    # Since we aren't forcing EOS, other tokens should not have -inf
    assert step < bs.max_len, (step, bs.max_len)
    assert has_no_inf(lprobs[:, a_idx])
    assert has_no_nan(lprobs[:, a_idx])

    # Since we've not yet reached min_len, EOS should have -inf.
    assert step < bs.min_len, (step, bs.min_len)
    assert_equal(lprobs[:, vocab_info.eos_idx], torch.tensor([-torch.inf, -torch.inf]))


def test_log_prob_running() -> None:
    vocab_info = VocabularyInfo(size=8, unk_idx=0, bos_idx=1, eos_idx=2, pad_idx=3)
    max_len = 20
    bs = search.BeamSearchStrategy(vocab_info=vocab_info, min_len=10, max_len=max_len)

    src_tokens = torch.tensor([[1, 2, 3, 4], [7, 8, 9, 10]], dtype=torch.int64)

    s = bs.new_search_job(src_tokens)

    # min_len < step < max_len
    step = 15

    # anything < size, not in the tokens.
    a_idx = 5

    t = torch.rand((2, vocab_info.size))

    lprobs = s._log_prob(t, step=step, max_len=max_len)

    raw = search.dec_out_to_log_prob(
        t, temperature=0.1, pad=vocab_info.pad_idx, bos=vocab_info.bos_idx
    )

    assert_equal(
        lprobs[:, vocab_info.unk_idx], raw[:, vocab_info.unk_idx] - bs.unk_penalty
    )
    assert_equal(lprobs[:, vocab_info.pad_idx], torch.tensor([-torch.inf, -torch.inf]))

    # Since we aren't forcing EOS, other tokens should not have -inf
    assert step < bs.max_len, (step, bs.max_len)
    assert has_no_inf(lprobs[:, a_idx])
    assert has_no_nan(lprobs[:, a_idx])

    # Since we aren't preventing EOS, EOS should not have -inf
    assert step > bs.min_len, (step, bs.min_len)
    assert has_no_inf(lprobs[:, vocab_info.eos_idx])
    assert has_no_nan(lprobs[:, vocab_info.eos_idx])


def test_log_prob_above_max() -> None:
    vocab_info = VocabularyInfo(size=8, unk_idx=0, bos_idx=1, eos_idx=2, pad_idx=3)
    max_len = 20
    bs = search.BeamSearchStrategy(vocab_info=vocab_info, min_len=10, max_len=max_len)

    src_tokens = torch.tensor([[1, 2, 3, 4], [7, 8, 9, 10]], dtype=torch.int64)
    s = bs.new_search_job(src_tokens)

    # anything < size, not in the tokens.
    a_idx = 5

    # force max_len trigger.
    step = 20
    t = torch.rand((2, vocab_info.size))
    lprobs = s._log_prob(t, step=20, max_len=max_len)

    assert_equal(lprobs[:, vocab_info.pad_idx], torch.tensor([-torch.inf, -torch.inf]))

    # Since we are forcing EOS, other tokens should have -inf
    assert step >= bs.max_len, (step, bs.max_len)
    assert lprobs[:, a_idx].tolist() == [-torch.inf, -torch.inf]
    # And EOS should not have -inf
    assert has_no_inf(lprobs[:, vocab_info.eos_idx])
    assert has_no_nan(lprobs[:, vocab_info.eos_idx])


def test_stretch_to_beams() -> None:
    t = torch.tensor(
        [
            [[3, 4], [5, 6]],
            [[13, 14], [15, 16]],
        ]
    )
    assert_close(
        search._stretch_to_beams(t, 2),
        [
            [[3, 4], [5, 6]],
            [[3, 4], [5, 6]],
            [[13, 14], [15, 16]],
            [[13, 14], [15, 16]],
        ],
    )
