"""Tests for the web deliverable HTML↔CSS↔JS static seam gate."""

from unittest.mock import patch

from agentcore.runtime.runs.contract import check_contract, needs_file_contents
from agentcore.runtime.runs.types import Deliverable
from agentcore.runtime.runs.web_seam import (
    WEB_SEAM_MISS_THRESHOLD,
    check_web_seam_failures,
    extract_css_selectors,
    extract_html_tokens,
    extract_inline_scripts,
    extract_inline_styles,
    extract_js_selectors,
    find_external_asset_refs,
    is_web_artifact_batch,
)


def test_is_web_artifact_batch_requires_html_plus_style_or_script():
    assert is_web_artifact_batch(["index.html", "style.css"])
    assert is_web_artifact_batch(["a.htm", "app.js"])
    assert is_web_artifact_batch(["site/index.html", "site/main.mjs", "notes.md"])
    assert not is_web_artifact_batch(["index.html"])
    assert not is_web_artifact_batch(["style.css", "app.js"])
    assert not is_web_artifact_batch(["report.md", "notes.md"])
    assert not is_web_artifact_batch([])


def test_needs_file_contents_true_for_web_batch_even_existence_only():
    paths = ["index.html", "style.css"]
    assert needs_file_contents(
        Deliverable(form="files"), landed_paths=paths
    )
    assert needs_file_contents(None, landed_paths=paths)
    # Markdown is a content surface → placeholder scan needs a read
    assert needs_file_contents(
        Deliverable(form="files"),
        landed_paths=["report.md"],
    )
    assert not needs_file_contents(
        Deliverable(form="files"),
        landed_paths=["main.py"],
    )


def test_extract_html_css_js_tokens():
    classes, ids = extract_html_tokens(
        '<div id="hero" class="card title">x</div><span class="card"></span>'
    )
    assert classes == {"card", "title"}
    assert ids == {"hero"}

    css_c, css_i = extract_css_selectors(
        "/* .ignored */ .card { color: #fff; } #hero { } .title.extra {}"
    )
    assert "card" in css_c and "title" in css_c and "extra" in css_c
    assert "hero" in css_i
    assert "fff" not in css_i  # hex color, not an id

    js_c, js_i = extract_js_selectors(
        "document.querySelector('.card');"
        "document.getElementById('hero');"
        "el.classList.add('active');"
        "document.getElementsByClassName('title');"
    )
    assert js_c >= {"card", "active", "title"}
    assert "hero" in js_i


def test_web_seam_mismatched_sample_fails_with_orphan_list():
    # ~66% of HTML classes missing from CSS — above the ~30% threshold (事故形态).
    html = """
    <html><body>
      <header class="site-header brand-bar">H</header>
      <main class="page-shell content-grid">
        <article class="card featured">A</article>
        <aside class="sidebar widget-stack">B</aside>
      </main>
      <footer class="site-footer legal-row">F</footer>
      <button id="cta" class="btn primary-cta">Go</button>
    </body></html>
    """
    # Only 3 of 10 classes styled; id also missing from CSS/JS.
    css = ".card { padding: 8px; } .btn { border: 0; } .primary-cta { color: red; }"
    contents = {"index.html": html, "style.css": css}
    failures = check_web_seam_failures(contents)
    assert len(failures) == 1
    msg = failures[0]
    assert "网页接缝静态检查未通过" in msg
    assert "`site-header`" in msg
    assert "`content-grid`" in msg
    assert "`cta`" in msg or "id" in msg

    v = check_contract(
        "已落盘网页",
        Deliverable(form="files"),
        files_written=2,
        workspace_paths=["index.html", "style.css"],
        artifact_contents=contents,
    )
    assert not v.ok
    assert any("网页接缝" in f for f in v.failures)


def test_web_seam_aligned_sample_passes():
    html = """
    <div id="app" class="shell">
      <button class="btn primary" id="save">Save</button>
    </div>
    """
    css = ".shell { display: flex; } .btn { } .primary { color: blue; } #app {} #save {}"
    js = "document.querySelector('.btn'); document.getElementById('save');"
    contents = {"index.html": html, "style.css": css, "app.js": js}
    assert check_web_seam_failures(contents) == []
    v = check_contract(
        "网页已写好",
        Deliverable(form="files"),
        files_written=3,
        workspace_paths=list(contents),
        artifact_contents=contents,
    )
    assert v.ok


def test_web_seam_js_only_hit_counts_as_aligned():
    # Class used only for JS (no CSS rule) is still a hit — 三方任一命中即可。
    html = '<button class="open-modal ghost">Open</button>'
    js = "document.querySelectorAll('.open-modal'); el.classList.add('ghost');"
    contents = {"index.html": html, "app.js": js}
    assert check_web_seam_failures(contents) == []


def test_web_seam_skips_non_web_and_partial_batches():
    assert check_web_seam_failures({"a.md": "# hi", "b.md": "x"}) == []
    assert check_web_seam_failures({"index.html": '<div class="x"></div>'}) == []
    assert check_web_seam_failures(None) == []
    assert check_web_seam_failures({}) == []


def test_web_seam_below_threshold_passes():
    # 1 orphan of 4 tokens = 25% ≤ 30% threshold → pass.
    assert WEB_SEAM_MISS_THRESHOLD == 0.30
    html = '<div class="a b c" id="root"></div>'
    css = ".a {} .b {} .c {}"  # id root missing → 1/4 = 25%
    assert check_web_seam_failures({"index.html": html, "style.css": css}) == []


def test_docs_only_deliverable_unaffected():
    v = check_contract(
        "报告已写",
        Deliverable(form="files"),
        files_written=1,
        workspace_paths=["report.md"],
        artifact_contents={"report.md": "# 报告\n正文"},
    )
    assert v.ok


def test_inline_style_and_script_count_toward_selector_pool():
    # index.html 内联样式 + 独立 main.js：修前会把内联 .shell / #app 判成挂空。
    html = """
    <html><head>
      <style>
        .shell { display: flex; }
        .btn.primary { color: blue; }
        #app { min-height: 100vh; }
      </style>
    </head>
    <body>
      <div id="app" class="shell">
        <button class="btn primary" id="save">Save</button>
      </div>
      <script>
        document.querySelector('.btn');
      </script>
    </body></html>
    """
    js = "document.getElementById('save'); document.getElementById('app');"
    assert extract_inline_styles(html) and ".shell" in extract_inline_styles(html)[0]
    assert any("querySelector" in s for s in extract_inline_scripts(html))
    contents = {"index.html": html, "main.js": js}
    assert check_web_seam_failures(contents) == []
    v = check_contract(
        "单文件风格页已写",
        Deliverable(form="files"),
        files_written=2,
        workspace_paths=list(contents),
        artifact_contents=contents,
    )
    assert v.ok


def test_external_stylesheet_or_script_skips_gate_and_logs():
    # Tailwind CDN 等远程资源无法静态验选择器 — 整批跳过，记 web_seam.skip_external。
    html = """
    <html><head>
      <link rel="stylesheet" href="https://cdn.tailwindcss.com">
      <script src="//unpkg.com/alpinejs"></script>
    </head>
    <body>
      <div class="flex items-center justify-between min-h-screen bg-slate-50">
        <button id="cta" class="px-4 py-2 rounded-lg">Go</button>
      </div>
    </body></html>
    """
    js = "console.log('local helper');"
    refs = find_external_asset_refs(html)
    assert any(r.startswith("https://") for r in refs)
    assert any(r.startswith("//") for r in refs)
    contents = {"index.html": html, "app.js": js}
    with patch("agentcore.runtime.runs.web_seam.logger") as mock_log:
        assert check_web_seam_failures(contents) == []
        mock_log.info.assert_called_once()
        assert mock_log.info.call_args.args[0] == "web_seam.skip_external"
        assert mock_log.info.call_args.kwargs["ref_count"] >= 2
    # 本地相对路径不算外部，门禁仍执行（错位仍 fail）。
    local_html = (
        '<link rel="stylesheet" href="./style.css">'
        '<div class="orphan-a orphan-b orphan-c orphan-d"></div>'
    )
    local_css = ".keep {}"
    assert find_external_asset_refs(local_html) == []
    assert check_web_seam_failures({"index.html": local_html, "style.css": local_css}) != []


def test_web_seam_fails_over_threshold_for_same_batch():
    html = (
        '<div class="a b c d e f g h i j k l m n o p q r s t u v w x y z'
        ' aa ab ac ad ae af ag ah ai aj ak al am an ao ap aq ar as at au av aw ax ay az'
        ' ba bb bc bd be bf bg bh bi bj bk bl bm bn bo bp bq br bs bt bu bv bw bx by bz'
        ' ca cb cc cd ce cf cg ch ci cj ck cl cm cn co cp cq cr cs ct cu cv cw cx cy cz'
        ' da db dc dd de df dg dh di dj dk dl dm dn do dp dq dr ds dt du dv dw dx dy dz'
        ' ea eb ec ed ee ef eg eh ei ej ek el em en eo ep eq er es et eu ev ew ex ey ez'
        ' fa fb fc fd fe ff fg fh fi fj fk fl fm fn fo fp fq fr fs ft fu fv fw fx fy fz'
        ' ga gb gc gd ge gf gg gh gi gj gk gl gm gn go gp gq gr gs gt gu gv gw gx gy gz'
        ' ha hb hc hd he hf hg hh hi hj hk hl hm hn ho hp hq hr hs ht hu hv hw hx hy hz'
        ' matched"></div>'
    )
    css = ".matched {}"
    contents = {
        "site/index.html": html,
        "site/styles.css": css,
        "site/main.js": "// no selectors",
    }
    failures = check_web_seam_failures(contents)
    assert failures
    assert any("未通过率" in f or "未命中率" in f for f in failures)


def test_web_seam_passes_aligned_batch():
    html = '<div class="hero card" id="app"></div>'
    css = ".hero {} .card {} #app {}"
    js = "document.querySelector('.hero');"
    contents = {
        "site/index.html": html,
        "site/styles.css": css,
        "site/main.js": js,
    }
    assert check_web_seam_failures(contents) == []
