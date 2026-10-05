# contextpack

把代码库按 token 预算打包成一份 LLM-ready 的上下文。纯本地、纯标准库，不联网、不花 token。

## 痛点

想让 LLM 帮你改代码，先得把代码喂给它：复制粘贴太散、直接丢整个仓库又超预算。contextpack 做这件脏活：

1. 扫描目录（跳过 `.git` / `node_modules` / `__pycache__` / 二进制文件）
2. 按相关性排序（入口文件优先：`main.py`、`README`、`package.json`…）
3. 按预算贪心装入，超了就从**最不重要的文件**开始截断/舍弃，并如实报告

## 安装

零依赖，Python 3.10+：

```bash
python -m contextpack ./my-repo --budget 8000 -o context.md
```

## 用法

```bash
# 打包当前目录，预算 8000 tokens，写文件
python -m contextpack . --budget 8000 -o context.md

# 先看"地图"：目录树 + 每个文件的 token 估算，不含内容
python -m contextpack . --map

# 只看打包计划（哪些收录/截断/舍弃），机器可读
python -m contextpack . --json --budget 2000

# 强制收录深层文件 / 排除测试目录
python -m contextpack . --include 'src/**' --exclude 'tests/**'

# 顺手复制到剪贴板（尽力而为，缺 pbcopy/xclip/wl-copy 会警告）
python -m contextpack . -o context.md --copy
```

输出示例（`--map`）：

```
目录：/path/to/repo（预算 8000 tokens，全部收录约需 12480）

├── README.md
├── contextpack.py
└── __main__.py

各文件估算：
    1450 tokens  README.md
    9200 tokens  contextpack.py
     830 tokens  __main__.py
```

## 排序规则（启发式，如实说明）

- 第 0 类：入口文件（`main.py`、`__main__.py`、`index.*`、`README`、`package.json`、`pyproject.toml`…）和 `--include` 强制文件
- 第 1 类：文档/配置（`.md`、`.toml`、`.yaml`…）
- 第 2 类：源代码
- 第 3 类：其他
- 同类内按 token 从小到大，尽量多装几个文件

## 诚实说明 / 已知限制

- **token 估算是粗略的**：按"字符数 ÷ 4"折算（`--chars-per-token` 可调），真实分词器（BPE）结果会有出入，中文尤其不准。预算请留余量。
- **相关性排序是启发式**：它不懂你的代码，只是按文件名/扩展名猜。关键文件请用 `--include` 点名。
- **默认不收录 `.env`**：防止把密钥喂给模型。如需收录请改名或显式处理。
- 二进制文件按扩展名 + 文件头空字节判断，极少数文本文件若含空字节会被误判跳过。
- 输出文件本身（`-o` 指定的路径）不会被重复装入。

## License

MIT，Copyright (c) 2026 ljiang9。
