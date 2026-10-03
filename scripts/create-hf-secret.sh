#!/usr/bin/env bash
# Create Secret hf-read-token in namespace tickertape: a Hugging Face READ token (fine-grained, read access to the
# private fine-tuned repo only) so laya can download it. The value comes from the environment and never touches the
# repo or the process list. The pod maps it to the name huggingface_hub reads (HF_TOKEN), see k8s/laya.yaml.
#   read -s HF_READ_TOKEN; export HF_READ_TOKEN; scripts/create-hf-secret.sh; unset HF_READ_TOKEN
set -euo pipefail
: "${HF_READ_TOKEN:?set HF_READ_TOKEN (read -s HF_READ_TOKEN; export HF_READ_TOKEN)}"
"$(dirname "$0")/kc.sh" apply -f - <<YAML
apiVersion: v1
kind: Secret
metadata: {name: hf-read-token, namespace: tickertape}
stringData:
  HF_READ_TOKEN: "$HF_READ_TOKEN"
YAML
