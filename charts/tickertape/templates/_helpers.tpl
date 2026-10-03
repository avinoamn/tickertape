{{/* Labels for object metadata. Pod templates and selectors deliberately keep only `app: <name>`: changing them would
     restart the pods (the model services reload their model on every start). */}}
{{- define "tickertape.labels" -}}
app.kubernetes.io/name: tickertape
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.Version | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end }}

{{/* usage: include "tickertape.image" (dict "name" "ner" "image" .Values.ner.image). The tag is required: no implicit
     version, never `latest`. */}}
{{- define "tickertape.image" -}}
{{ .image.repository }}:{{ required (printf "%s.image.tag is required" .name) .image.tag }}
{{- end }}

{{- define "tickertape.pullSecrets" -}}
{{- with .Values.image.pullSecrets }}
imagePullSecrets:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{/* storageClassName line, omitted when empty so the cluster default applies */}}
{{- define "tickertape.storageClass" -}}
{{- if . }}
storageClassName: {{ . }}
{{- end }}
{{- end }}
