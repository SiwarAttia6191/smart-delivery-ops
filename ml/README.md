# ML / AI Layer (Phase 3)

```
ml/
├── features/     # demand_features, route_features, pricing_features
├── models/       # demand_model, eta_model, model_registry
├── training/     # train_demand_model, train_eta_model, evaluate_model
├── pipelines/    # demand_pipeline, inference_pipeline
└── artifacts/    # trained model artifacts (gitignored)
```

Models planned: hourly demand forecasting by region (Prophet / LightGBM),
ETA regression, supply-imbalance detection. Design: docs/ml_design.md.
