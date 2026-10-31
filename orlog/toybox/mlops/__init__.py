"""
orlog/toybox/mlops/ — Building, training, evaluating, and deploying machine learning models or AI-powered systems in production.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "mlops"
DESCRIPTION = "Building, training, evaluating, and deploying machine learning models or AI-powered systems in production."

AGENT_PACK = ['experiment_tracker', 'training_job_scheduler', 'dataset_versioner', 'model_registry_manager', 'eval_benchmark_runner', 'production_drift_monitor', 'prompt_version_controller', 'cost_per_inference_optimizer']

KEY_METRICS = ['model_accuracy', 'training_cost_per_run', 'inference_latency_p99', 'token_cost_per_query', 'data_drift_score', 'model_freshness_days', 'gpu_utilization_pct']

COMMON_APIS = ['Hugging Face Hub API', 'Weights & Biases API', 'MLflow API', 'AWS SageMaker API', 'Google Vertex AI API', 'Anthropic API', 'Replicate API', 'Modal', 'LakeFS API', 'Evidently AI API']

REQUIRED_ENV = ['ANTHROPIC_API_KEY', 'WANDB_API_KEY']
