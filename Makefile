# OUSSAMA Cutter — أوامر التطوير الموحّدة
.DEFAULT_GOAL := help
.PHONY: help install dev-install lint fmt test test-fast cov clean run docker-up docker-down hooks audit

help:  ## اعرض هذه القائمة
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## ثبّت تبعيات التشغيل
	pip install -r requirements.txt

dev-install:  ## ثبّت تبعيات التطوير + hooks
	pip install -r requirements-dev.txt
	pre-commit install

hooks:  ## شغّل كل الفحوصات على كل الملفات
	pre-commit run --all-files

lint:  ## فحص الكود دون تعديل
	ruff check .
	ruff format --check .

fmt:  ## صحّح ونسّق الكود
	ruff check --fix .
	ruff format .

test:  ## كل الاختبارات
	pytest -v

test-fast:  ## اختبارات سريعة (توقف عند أول فشل)
	pytest -x -q

cov:  ## تقرير التغطية
	pytest --cov=scripts --cov=webui --cov-report=term-missing --cov-report=html

audit:  ## فحص ثغرات التبعيات
	pip-audit || true

run:  ## شغّل الواجهة
	python webui/app.py

docker-up:  ## شغّل عبر Docker
	docker compose up --build

docker-down:
	docker compose down

clean:  ## احذف الملفات المؤقتة
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage build dist *.egg-info
