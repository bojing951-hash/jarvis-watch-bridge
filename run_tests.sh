#!/bin/bash
# 运行 Jarvis Watch Bridge 全部自动化测试
cd "$(dirname "$0")"
exec python3 -m unittest discover -s tests -v
