PYTHON ?= python
UV ?= uv

.PHONY: help deps test lint fmt fmt-check check build

help:
	@echo "可用命令："
	@echo "  make deps       安装/同步依赖（含开发依赖，使用 uv）"
	@echo "  make test       运行全部测试（pytest）"
	@echo "  make lint       运行静态检查（ruff）"
	@echo "  make fmt        使用 black 自动格式化代码"
	@echo "  make fmt-check  检查格式（black --check，不修改文件）"
	@echo "  make check      lint + fmt-check + test 一次性运行"
	@echo "  make build      构建项目（wheel 等，使用 uv build）"

deps:
	$(UV) sync --dev

test:
	$(UV) run pytest

lint:
	$(UV) run ruff check vision_ai_tools tests

fmt:
	$(UV) run black vision_ai_tools tests

fmt-check:
	$(UV) run black --check vision_ai_tools tests

check: lint fmt-check test

build:
	$(UV) build

