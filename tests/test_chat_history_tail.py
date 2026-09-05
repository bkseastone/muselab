import pytest

from backend.chat_history import read_tail_lines


@pytest.mark.parametrize('raw,n,expected', [
    (b'a\nb\nc\n', 1, ['c']), (b'a\nb\nc', 2, ['b', 'c']),
    (b'a\n \n\nb\n\n', 2, ['a', 'b']), (b'\n\n', 2, []),
    (b'a\nb\n', 0, []), (b'one long line\n', 1, ['one long line']),
])
@pytest.mark.parametrize('block', [1, 3, 65536])
def test_tail_nonempty_lines_across_block_boundaries(tmp_path, raw, n, expected, block):
    path = tmp_path / 'tail.jsonl'
    path.write_bytes(raw)
    assert read_tail_lines(path, n, block=block) == expected


def test_tail_preserves_unicode_and_large_line(tmp_path):
    path = tmp_path / 'tail.jsonl'
    line = '合成' * 100000
    path.write_text('before\n' + line + '\n\n', encoding='utf-8')
    assert read_tail_lines(path, 1, block=31) == [line]
