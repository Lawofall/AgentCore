"""Skill body: prompts (本桌规矩怎么写成常驻 / 按需 / 碰到文件)."""

from __future__ import annotations

_PROMPTS = """\
<规矩>
用户要给这张桌立规矩、改常驻 / 按需 / 碰到文件时走本条。写普通稿、写代码、干活不走本条。

【落盘】本桌规矩是工作区 ``AgentCore/rules/*.md``（可分子目录）。用 ``write`` / ``edit`` 写这篇，下一回合按文件生效。\
账号工具箱里已有的篇是跨桌规矩，这两把笔改不了那份。\
``skills/*.json`` 不是规矩。

【带法】文件开头的 YAML 决定怎么进回合。缺 ``apply`` 时按需。空 ``description`` 的按需不进目录。

---
apply: always
description: 一行摘要
paths: **/*.py
---

``apply`` 三值：``always`` 常驻进 ``<设定>``（宜短）；``on_demand`` 按需进 ``<按需目录>``，用 ``consult(文件名去.md)`` 取正文；``paths`` 碰到真实路径才带全文。子目录 ``AgentCore/rules/team/约定.md`` 的查阅名是 ``team/约定``。

【常驻配额】常驻写太长会被拒；改成按需或删减后再写。
</规矩>"""
