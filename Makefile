# Makefile — أوامر التطوير المختصرة. اكتب `make` أو `make help` لعرض القائمة.
.DEFAULT_GOAL := help
.PHONY: help install dev-install lint fmt test cov audit run docker-up docker-down clean

PYTHON ?= python3
PIP    ?= $(PYTHON) -m pip

help: ## عرض هذه القائمة
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## تثبيت متطلبات التشغيل
	$(PIP) install -U pip
	@if [ -f requirements.txt ]; then $(PIP) install -r requirements.txt; fi

dev-install: install ## تثبيت أدوات التطوير وتفعيل pre-commit
	$(PIP) install ruff pre-commit pytest pytest-cov pip-audit
	pre-commit install

lint: ## فحص الكود دون تعديله
	ruff check .
	ruff format --check .

fmt: ## تنسيق الكود وإصلاح ما يمكن إصلاحه تلقائياً
	ruff check . --fix
	ruff format .

test: ## تشغيل الاختبارات
	pytest -q

cov: ## تشغيل الاختبارات مع تقرير التغطية
	pytest --cov=. --cov-report=term-missing --cov-report=html

audit: ## تدقيق أمني للاعتماديات
	pip-audit || true

run: ## تشغيل التطبيق
	$(PYTHON) main.py

docker-up: ## تشغيل الحاويات
	docker compose up -d --build

docker-down: ## إيقاف الحاويات
	docker compose down

clean: ## حذف الملفات المؤقتة ومخلّفات البناء
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage build dist *.egg-info
