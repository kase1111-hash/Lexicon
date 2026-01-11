"""Monthly phylogenetic tree reconstruction DAG."""

from datetime import datetime, timedelta

# Note: Airflow imports commented out until dependencies are installed
# from airflow import DAG
# from airflow.operators.python import PythonOperator

default_args = {
    "owner": "linguistic-stratigraphy",
    "depends_on_past": False,
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
}

# DAG configuration for monthly phylogenetics
# Schedule: 1st of each month
# Tasks:
# 1. extract_cognate_matrices - Prepare cognate matrices from graph
# 2. run_beast - Run BEAST2 phylogenetic inference (48h timeout)
# 3. compare_trees - Compare to baseline trees (Robinson-Foulds distance)

# Uncomment when Airflow is installed:
# with DAG(
#     'monthly_phylogenetics',
#     default_args=default_args,
#     description='Monthly phylogenetic tree reconstruction',
#     schedule_interval='0 0 1 * *',
#     start_date=datetime(2024, 1, 1),
#     catchup=False,
#     tags=['analysis'],
# ) as dag:
#     pass
