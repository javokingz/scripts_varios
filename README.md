# Dashboard ECS con Streamlit

Prototipo local de solo lectura para comparar cargas de microservicios ECS en distintas cuentas AWS usando perfiles de AWS CLI, roles y SSO. No guarda credenciales ni modifica servicios.

## Ejecutar

Requiere Python 3.10+ y AWS CLI configurado en la misma máquina y usuario que ejecuta Streamlit.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
aws sso login --profile mi-perfil
streamlit run app.py --server.address 127.0.0.1
```

Abre http://localhost:8501. Elige perfil (o filtra por número de cuenta), región, clúster y servicios. El número de cuenta filtra perfiles ya configurados: no concede acceso ni crea roles. STS verifica y muestra la cuenta efectiva antes de consultar ECS. Para cerrar, Ctrl+C.

Boto3 usa `~/.aws/config`, `~/.aws/credentials` y el caché SSO de AWS CLI, respetando `AWS_CONFIG_FILE` y `AWS_SHARED_CREDENTIALS_FILE`. No copies secretos al proyecto. Los perfiles con `role_arn`/`source_profile`, `credential_process` o sesiones SSO se resuelven mediante la cadena de credenciales de Boto3. Si el rol exige MFA, puede pedir el código en la terminal. Reinicia Streamlit si agregas perfiles o cambias su configuración.

Ejemplo de configuración (identificadores ficticios):

```ini
[sso-session empresa]
sso_start_url = https://mi-empresa.awsapps.com/start
sso_region = us-east-1
sso_registration_scopes = sso:account:access

[profile produccion]
sso_session = empresa
sso_account_id = 123456789012
sso_role_name = ReadOnlyAccess
region = us-east-1

[profile cuenta-con-role]
role_arn = arn:aws:iam::210987654321:role/ECSMonitoringReadOnly
source_profile = produccion
region = us-east-1
```

Para configurar SSO desde cero: `aws configure sso --profile mi-perfil`. Si una sesión expira, ejecuta `aws sso login --profile mi-perfil` y pulsa **Actualizar datos**. Para roles encadenados, inicia sesión en el perfil SSO de origen.

## Qué muestra

- Inventario actual por servicio: tareas deseadas, en ejecución, pendientes, estado y definición de tarea.
- CPU y memoria porcentuales por servicio de `AWS/ECS`, comparables en gráficas.
- Últimas 24 horas, 7, 14 o 30 días, con intervalos de 5, 15, 30 o 60 minutos respectivamente. Todas las fechas se muestran en UTC.
- Promedio o máximo por intervalo; el resumen se calcula sobre esos puntos agregados (no sobre muestras originales).
- Historial opcional de `DesiredTaskCount` y `RunningTaskCount` de `ECS/ContainerInsights`, cuando estén publicados. Habilitar la casilla solo consulta métricas existentes; no activa Container Insights.
- CSV separados para inventario y series históricas, con cuenta, región y clúster.

Las cifras de capacidad superiores son actuales, no un histórico. CPU y memoria representan utilización de recursos asignados al servicio, no porcentajes por contenedor individual. Un servicio sin tareas activas puede no publicar puntos. Los huecos se conservan: no se convierten en cero ni se interpolan. Container Insights no reconstruye datos anteriores a su activación. Las consultas de CloudWatch pueden generar cargos según el uso y la configuración de AWS.

El inventario se consulta en cada interacción. Las series se mantienen únicamente en la sesión del navegador hasta cambiar los filtros o pulsar **Actualizar datos**; no hay refresco automático. La aplicación pagina inventarios y resultados de CloudWatch y divide consultas en lotes. Para clústeres grandes, selecciona pocos servicios al inicio.

## Permisos

El usuario/rol necesita estas acciones de lectura en cada cuenta (ejemplo para un administrador IAM):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["ecs:ListClusters", "ecs:ListServices", "ecs:DescribeServices", "cloudwatch:GetMetricData"],
    "Resource": "*"
  }]
}
```

También se utiliza `sts:GetCallerIdentity` para verificar la cuenta. Si asumes un rol, el perfil de origen debe poder ejecutar `sts:AssumeRole` y el rol destino debe confiar en él. El acceso SSO depende del permission set asignado. El ejemplo cubre descubrimiento entre clústeres; tu administrador puede restringir recursos donde AWS lo admita.

## Validación

```bash
python -m unittest discover -s tests -v
```

Las pruebas usan respuestas simuladas y no requieren credenciales. La validación real depende de perfiles locales autorizados y de métricas publicadas. Este prototipo está pensado para ejecución local; todos los usuarios de un mismo servidor Streamlit usarían los perfiles del proceso servidor.

Referencias: [credenciales Boto3](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html), [utilización ECS](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service_utilization.html), [Container Insights ECS](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Container-Insights-metrics-ECS.html), [GetMetricData](https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/API_GetMetricData.html).
