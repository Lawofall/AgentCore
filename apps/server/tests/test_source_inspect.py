"""Unit tests for ``code_execute`` source-inspect routing (dump → file_read, regex → grep)."""

from agentcore.tools.builtin.source_inspect import source_inspect_match


def test_source_inspect_matches_direct_dump():
    hit = source_inspect_match(
        "print(open('apps/server/agentcore/observability/catalog.py', encoding='utf-8').read()[:3000])"
    )
    assert hit is not None
    assert hit.kind == "dump"


def test_source_inspect_matches_assigned_slice_dump():
    hit = source_inspect_match(
        "src = open('scripts/sync_log_event_registry.py', encoding='utf-8').read()\n"
        "print(src[:3000])"
    )
    assert hit is not None
    assert hit.kind == "dump"


def test_source_inspect_matches_path_read_text_dump():
    hit = source_inspect_match(
        "from pathlib import Path\n"
        "print(Path('apps/server/foo.py').read_text(encoding='utf-8')[:500])"
    )
    assert hit is not None
    assert hit.kind == "dump"


def test_source_inspect_matches_with_open_handle_dump():
    hit = source_inspect_match(
        "with open('apps/server/foo.py', encoding='utf-8') as f:\n"
        "    print(f.read()[:200])"
    )
    assert hit is not None
    assert hit.kind == "dump"


def test_source_inspect_matches_regex_scan_of_source():
    hit = source_inspect_match(
        "src = open('apps/server/agentcore/observability/catalog.py', encoding='utf-8').read()\n"
        "specs = re.findall(r\"EventSpec\\s*\\(\\s*name\\s*=\\s*'([^']+)'\", src)\n"
        "print(len(specs), specs[:20])"
    )
    assert hit is not None
    assert hit.kind == "grep"


def test_source_inspect_prefers_dump_when_both_present():
    hit = source_inspect_match(
        "src = open('apps/server/foo.py').read()\n"
        "print(src[:100])\n"
        "print(len(re.findall(r'TODO', src)))"
    )
    assert hit is not None
    assert hit.kind == "dump"


def test_source_inspect_allows_short_compute():
    assert source_inspect_match("print(1+1)") is None
    assert source_inspect_match("print('hello')") is None


def test_source_inspect_allows_ast_parse():
    assert (
        source_inspect_match(
            "import ast\n"
            "tree = ast.parse(open('apps/server/foo.py', encoding='utf-8').read())\n"
            "print(len(tree.body))"
        )
        is None
    )


def test_source_inspect_allows_pandas_csv():
    assert (
        source_inspect_match(
            "import pandas as pd\n"
            "df = pd.read_csv('bills.csv')\n"
            "print(df.head())"
        )
        is None
    )


def test_source_inspect_allows_pandas_even_with_csv_peek():
    assert (
        source_inspect_match(
            "import pandas as pd\n"
            "print(open('bills.csv').read()[:200])\n"
            "df = pd.read_csv('bills.csv')"
        )
        is None
    )


def test_source_inspect_does_not_treat_apps_path_as_write_mode():
    """`open('apps/...')` is a path, not mode='a'."""
    hit = source_inspect_match(
        "print(open('apps/server/foo.py', encoding='utf-8').read()[:80])"
    )
    assert hit is not None
    assert hit.kind == "dump"


def test_source_inspect_skips_when_explicit_write_mode_present():
    assert (
        source_inspect_match(
            "src = open('apps/server/foo.py').read()\n"
            "out = open('apps/server/out.py', 'w', encoding='utf-8')\n"
            "print(src[:80])"
        )
        is None
    )


def test_source_inspect_allows_read_then_write_transform():
    assert (
        source_inspect_match(
            "src = open('apps/server/foo.py').read()\n"
            "Path('apps/server/foo.py').write_text(src.replace('a', 'b'))"
        )
        is None
    )


def test_source_inspect_allows_regex_when_writing_output():
    assert (
        source_inspect_match(
            "src = open('apps/server/foo.py').read()\n"
            "hits = re.findall(r'TODO', src)\n"
            "Path('AgentCore/文档/工作稿/todos.md').write_text('\\n'.join(hits))"
        )
        is None
    )


def test_source_inspect_ignores_regex_on_memory_string():
    assert (
        source_inspect_match(
            "text = 'EventSpec(name=\"a\") EventSpec(name=\"b\")'\n"
            "print(re.findall(r'EventSpec', text))"
        )
        is None
    )
