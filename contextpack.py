#!/usr/bin/env python3
"""contextpack: 把代码库按 token 预算打包成一份 LLM-ready 的上下文。

纯标准库、纯本地：扫描目录 -> 按相关性排序 -> 按预算贪心装入 -> 输出单个 Markdown。
"""
import argparse
import fnmatch
import json
import math
import os
import shutil
import subprocess
import sys

VERSION = "0.1.0"

SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "build",
             ".venv", "venv", "target", ".idea", ".vscode", ".tox",
             ".eggs", "eggs", "__pypackages__"}
BINARY_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
                ".woff", ".woff2", ".ttf", ".otf", ".eot",
                ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
                ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".o", ".a",
                ".mp3", ".mp4", ".mov", ".avi", ".wav", ".flac",
                ".sqlite", ".db", ".lock"}
ENTRY_NAMES = {"readme.md", "readme.rst", "readme.txt", "main.py", "__main__.py",
               "index.js", "index.ts", "index.jsx", "index.tsx", "index.html",
               "app.py", "cli.py", "package.json", "pyproject.toml", "setup.py",
               "setup.cfg", "requirements.txt", "cargo.toml", "go.mod",
               "makefile", "dockerfile", "docker-compose.yml"}
DOC_EXTS = {".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".json",
            ".cfg", ".ini", ".example"}
SRC_EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java",
            ".c", ".h", ".cpp", ".hpp", ".rb", ".php", ".sh", ".css",
            ".html", ".sql", ".vue", ".svelte", ".swift", ".kt", ".lua"}
# 可能含密钥的文件默认不收录（README 里如实说明）
SECRET_NAMES = {".env"}


def is_binary(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in BINARY_EXTS:
        return True
    try:
        with open(path, "rb") as f:
            head = f.read(8192)
    except OSError:
        return True
    return b"\x00" in head


def rel(p, root):
    return os.path.relpath(p, root).replace(os.sep, "/")


def matches_any(path, patterns):
    return any(fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(os.path.basename(path), pat)
               for pat in patterns)


def collect(root, include=(), exclude=(), output_abs=None):
    files = []
    skipped_binary = []
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        # 跳过隐藏目录已在上面处理；保留 .github 这类显式需求可用 --include
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            r = rel(full, root)
            if output_abs and os.path.abspath(full) == output_abs:
                continue  # 不把本次输出文件自己装进去
            if os.path.basename(fn) in SECRET_NAMES:
                continue
            if exclude and matches_any(r, exclude):
                continue
            if is_binary(full):
                skipped_binary.append(r)
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            files.append((r, full, size))
    if include:
        # --include 强制把符合模式的文件拉回（即使之前被目录规则跳过也补扫不到，
        # 这里只对已收集的文件提权；文档里如实说明）
        pass
    return files, skipped_binary


def classify(r, forced):
    base = os.path.basename(r).lower()
    if r in forced:
        return 0
    if base in ENTRY_NAMES:
        return 0
    ext = os.path.splitext(base)[1]
    if ext in DOC_EXTS:
        return 1
    if ext in SRC_EXTS:
        return 2
    return 3


def estimate_tokens(text, cpt):
    return max(1, math.ceil(len(text) / cpt))


def fence_for(text):
    if "```" not in text:
        return "```"
    n = 4
    while "`" * n in text:
        n += 1
    return "`" * n


def build_tree(paths):
    tree = {}
    for p in sorted(paths):
        node = tree
        for part in p.split("/")[:-1]:
            node = node.setdefault(part, {})
        node[p.split("/")[-1]] = None
    lines = []
    def walk(node, prefix=""):
        items = sorted(node.items(), key=lambda kv: (kv[1] is None, kv[0]))
        for i, (name, child) in enumerate(items):
            last = i == len(items) - 1
            lines.append(prefix + ("└── " if last else "├── ") + name)
            if child is not None:
                walk(child, prefix + ("    " if last else "│   "))
    walk(tree)
    return "\n".join(lines)


def pack(root, budget, cpt, include=(), exclude=(), output_abs=None):
    files, skipped_binary = collect(root, include, exclude, output_abs)
    if not files:
        return None, "目录为空或没有可扫描的文件"
    forced = {r for r, _, _ in files if include and matches_any(r, include)}
    entries = []
    for r, full, size in files:
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            return None, "读取文件失败 %s：%s" % (r, e)
        cls = classify(r, forced)
        entries.append({"rel": r, "text": text,
                        "tokens": estimate_tokens(text, cpt),
                        "cls": cls})
    entries.sort(key=lambda e: (e["cls"], e["tokens"], e["rel"]))
    remaining = budget
    plan = []
    for e in entries:
        cost = e["tokens"]
        if remaining >= cost:
            e["status"] = "收录"
            e["used"] = cost
            remaining -= cost
        elif remaining > 50:
            # 截断装入：按剩余预算折算字符数
            keep_chars = remaining * cpt
            e["text"] = e["text"][:keep_chars] + "\n…[已截断，仅收录前 %d tokens]" % remaining
            e["status"] = "截断"
            e["used"] = remaining
            remaining = 0
        else:
            e["status"] = "舍弃"
            e["used"] = 0
        plan.append(e)
    return {"entries": plan, "skipped_binary": skipped_binary,
            "budget": budget, "used": budget - remaining}, None


def render_markdown(root_name, plan, tree_text):
    out = []
    out.append("# 上下文包：%s" % root_name)
    out.append("")
    out.append("> 由 contextpack 生成。请注意：token 数为 字符/%d 的粗略估算，"
               "真实分词器结果会有出入。" % plan["cpt"])
    out.append("")
    out.append("## 目录结构")
    out.append("")
    out.append("```")
    out.append(tree_text)
    out.append("```")
    out.append("")
    out.append("## 文件（按相关性排序，共 %d 个，预算 %d tokens，已用 %d）"
               % (len(plan["entries"]), plan["budget"], plan["used"]))
    out.append("")
    for e in plan["entries"]:
        if e["status"] == "舍弃":
            continue
        fence = fence_for(e["text"])
        out.append("### `%s`（%s，约 %d tokens）" % (e["rel"], e["status"], e["used"]))
        out.append("")
        out.append(fence)
        out.append(e["text"])
        out.append(fence)
        out.append("")
    dropped = [e["rel"] for e in plan["entries"] if e["status"] == "舍弃"]
    truncated = [e["rel"] for e in plan["entries"] if e["status"] == "截断"]
    if truncated or dropped or plan["skipped_binary"]:
        out.append("## 打包报告")
        out.append("")
        if truncated:
            out.append("- 截断（超出预算，只收录了前部）：")
            for r in truncated:
                out.append("  - %s" % r)
        if dropped:
            out.append("- 舍弃（预算不足未收录）：")
            for r in dropped:
                out.append("  - %s" % r)
        if plan["skipped_binary"]:
            out.append("- 跳过二进制文件（%d 个）：%s" %
                       (len(plan["skipped_binary"]),
                        "、".join(plan["skipped_binary"][:10])))
        out.append("")
    return "\n".join(out)


def copy_to_clipboard(text):
    for cmd, args in (("pbcopy", []), ("xclip", ["-selection", "clipboard"]),
                      ("wl-copy", [])):
        if shutil.which(cmd):
            try:
                subprocess.run([cmd] + args, input=text.encode("utf-8"),
                               timeout=10, check=True)
                return True, cmd
            except (subprocess.SubprocessError, OSError):
                continue
    return False, None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="contextpack",
                                 description="把代码库按 token 预算打包成 LLM-ready 上下文（纯本地）")
    ap.add_argument("repo", nargs="?", default=".",
                    help="要打包的仓库目录（默认当前目录）")
    ap.add_argument("--budget", type=int, default=8000, help="token 预算（默认 8000）")
    ap.add_argument("--chars-per-token", type=int, default=4, dest="cpt",
                    help="每 token 按多少字符估算（默认 4，仅为粗略估算）")
    ap.add_argument("-o", "--output", help="输出 Markdown 文件路径（不填则打印到 stdout）")
    ap.add_argument("--include", action="append", default=[],
                    help="强制收录的 glob 模式，可重复，如 --include 'src/**'")
    ap.add_argument("--exclude", action="append", default=[],
                    help="排除的 glob 模式，可重复")
    ap.add_argument("--map", action="store_true",
                    help="只输出目录树 + 每个文件的 token 估算（不含内容），用于规划预算")
    ap.add_argument("--json", action="store_true", help="输出打包计划 JSON")
    ap.add_argument("--copy", action="store_true", help="把结果复制到剪贴板（尽力而为）")
    ap.add_argument("--version", action="version", version="contextpack " + VERSION)
    args = ap.parse_args(argv)

    if args.budget <= 0:
        sys.stderr.write("error: --budget 必须为正整数\n")
        return 1
    root = os.path.abspath(args.repo)
    if not os.path.isdir(root):
        sys.stderr.write("error: 目录不存在：%s\n" % args.repo)
        return 1
    output_abs = os.path.abspath(args.output) if args.output else None

    plan, err = pack(root, args.budget, args.cpt, args.include, args.exclude, output_abs)
    if err:
        sys.stderr.write("error: %s\n" % err)
        return 1
    plan["cpt"] = args.cpt
    tree_text = build_tree([e["rel"] for e in plan["entries"]])

    if args.json:
        doc = {"repo": root, "budget": args.budget, "chars_per_token": args.cpt,
               "used_tokens": plan["used"],
               "files": [{"path": e["rel"], "class": e["cls"],
                          "tokens": e["tokens"], "used": e["used"],
                          "status": e["status"]} for e in plan["entries"]],
               "skipped_binary": plan["skipped_binary"]}
        text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(text)
        else:
            sys.stdout.write(text)
    elif args.map:
        lines = ["目录：%s（预算 %d tokens，全部收录约需 %d）"
                 % (root, args.budget, sum(e["tokens"] for e in plan["entries"])),
                 "", tree_text, "", "各文件估算："]
        for e in plan["entries"]:
            lines.append("  %6d tokens  %s" % (e["tokens"], e["rel"]))
        if plan["skipped_binary"]:
            lines.append("跳过二进制文件 %d 个" % len(plan["skipped_binary"]))
        text = "\n".join(lines) + "\n"
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(text)
        else:
            sys.stdout.write(text)
    else:
        text = render_markdown(os.path.basename(root) or root, plan, tree_text)
        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(text)
            except OSError as e:
                sys.stderr.write("error: 写入文件失败：%s\n" % e)
                return 1
        else:
            sys.stdout.write(text)

    if args.copy:
        ok, cmd = copy_to_clipboard(text)
        if ok:
            sys.stderr.write("已复制到剪贴板（经 %s）。\n" % cmd)
        else:
            sys.stderr.write("警告：未找到 pbcopy / xclip / wl-copy，无法复制到剪贴板。\n")

    dropped = sum(1 for e in plan["entries"] if e["status"] == "舍弃")
    sys.stderr.write("打包完成：%d 个文件，预算 %d tokens，已用 %d，舍弃 %d。\n"
                     % (len(plan["entries"]), args.budget, plan["used"], dropped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
