# jsonl-byte-shards 中文快速上手

按字节上限把本地 JSONL 拆成完整记录组成的分片，验证清单后再合并，保留原始字节。
LF、CRLF、中文 UTF-8、数字写法和末尾无换行均保留，不会重新序列化 JSON。
单条记录超过分片预算时直接报错，不拆开记录。

## 安装与示例

需要 Python 3.11+，仅用标准库。下载仓库后在目录中运行，无需 pip 安装。
Windows 可以把 `python` 换成 `py -3.11`。

```console
python jsonl_byte_shards.py split examples/sample.jsonl --out-dir sample-parts --part-bytes 45
python jsonl_byte_shards.py verify sample-parts/manifest.json
python jsonl_byte_shards.py join sample-parts/manifest.json --out restored.jsonl
```

输出目录 `sample-parts` 和输出文件 `restored.jsonl` 必须不存在，父目录必须已存在。
分片依次命名为 `part-00001.jsonl` 等，最后写入 `manifest.json`，清单只包含字节数、
记录数、连续行号区间、SHA256 和分片预算，不记录原始机器路径。

可重复运行、不产生固定路径输出的演示：

```console
python examples/demo.py roundtrip
python examples/demo.py tamper
python examples/demo.py occupied
```

预期退出码分别为 0、1、2。后两个演示有意展示篡改检测和已有输出拒绝。
全部演示数据都是合成数据。正文、JSON 解析片段不会进入工具的报告。

## 输入、验证与限制

- 输入必须非空，每个以 LF 分隔的记录是一个完整 UTF-8 JSON 值，可使用 CRLF，
  最后一条允许无 LF。支持对象、数组、字符串、数字、布尔值和 null。
- 拒绝 BOM、空行、重复键、无效 UTF-8、NaN/Infinity。数字只验证语法，保留原始写法；
  解析器自身的嵌套深度限制仍适用。
- 原始输入/合并分片最多 64 MiB，单条记录含换行最多 1 MiB，最多 100000 条、1000 片，
  清单最多 1 MiB，`--part-bytes` 范围 1 到 16 MiB。MiB 是 1048576 字节。
- 验证会重新解析分片，检查实际字节数、每片及整体哈希、连续行区间、贪心装片和记录边界。
  只有最后一片可缺少末尾 LF。未列入清单的文件不读取、不合并。
- 禁止绝对/越界/嵌套分片名，以及观察到的软链接、重解析点和多硬链接输入。
  任意已有输出路径均拒绝，包括悬空软链接和硬链接。
- 合并使用已验证并捕获的字节，暂存并复核后才发布；需要同一文件系统支持硬链接。
  不会验证后盲目重新读取可能已变化的分片。
- 拆分目录发布不保证事务性：发布中的 I/O 失败可能留下新的不完整目录，清单最后写入。
  该工具按运行期间本地输入保持不变设计，不是防恶意竞态的文件系统沙箱。
- 多份受限数据可同时留在内存，64 MiB 输入上限不是进程内存上限。成功也不保证断电持久性。
- 清单通过只说明相对该清单的内部完整性；同时替换数据和清单仍可通过，不代表可信来源。

## 退出码与检查

| 退出码 | 含义 |
| --- | --- |
| 0 | 拆分/验证/合并成功 |
| 1 | 结构支持的清单与分片内容、哈希、行区间或装片策略不一致 |
| 2 | 输入无效、清单不支持、硬上限、路径/输出冲突或 I/O/环境错误 |

缺失分片属于 I/O 错误 2；分片内 JSON 无效属于内容错误 1；硬上限优先。
验证失败不发布合并文件。

```console
python -m unittest discover -s tests -v
```

测试使用临时目录；Windows 缺少软链接权限时会明确跳过相应测试，不算已验证通过。
详细清单结构、边界和替代方案见 [English README](README.md)、[RESEARCH](RESEARCH.md)
及 [TESTING](TESTING.md)。MIT，2026 maomao-1937。
