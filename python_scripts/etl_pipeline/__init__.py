"""Cron-driven runner for the intelli-wealth-backend ETL handoff queue.

Reserves a batch of RTA report files from the handoff API, downloads each from
S3, loads them through the EXISTING bronze/silver/gold loaders, and reports
each file's outcome back. See
docs/superpowers/specs/2026-08-31-etl-automation-pipeline-design.md.

This package never replaces the Streamlit app (python_scripts/app.py); it is a
second caller of the same loaders.
"""
