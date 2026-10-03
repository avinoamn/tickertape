{{/* Labels for object metadata. Pod templates and selectors deliberately keep only `app: <name>`: changing them would
     restart the pods (the model services reload their model on every start). */}}
{{- define "tickertape.labels" -}}
app.kubernetes.io/name: tickertape
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end }}

{{/* usage: include "tickertape.image" (dict "image" .Values.ner.image "root" .) ; the tag defaults to the chart's appVersion */}}
{{- define "tickertape.image" -}}
{{ .image.repository }}:{{ .image.tag | default .root.Chart.AppVersion }}
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
