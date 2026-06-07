#!/bin/bash
cd /workspace/workspaces/living-api-contract-guardian
python -m pytest tests/property/test_campaigns_properties.py -v --tb=short
