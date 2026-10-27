# Dashboard ECS, RDS y ElastiCache con Streamlit

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

- Inventario actual por servicio: tareas deseadas, en ejecución, pendientes, estado, CPU mean (%) y CPU máximo (%).
- CPU y memoria porcentuales por servicio de `AWS/ECS`, comparables en gráficas.
- Últimas 24 horas, 7, 14 o 30 días, con intervalos de 5, 15, 30 o 60 minutos respectivamente. Todas las fechas se muestran en UTC.
- Promedio o máximo por intervalo; el resumen se calcula sobre esos puntos agregados (no sobre muestras originales).
- Historial opcional de `DesiredTaskCount` y `RunningTaskCount` de `ECS/ContainerInsights`, cuando estén publicados. Habilitar la casilla solo consulta métricas existentes; no activa Container Insights.
- CSV separados para inventario y series históricas, con cuenta, región y clúster.

Las cifras de capacidad superiores son actuales, no un histórico. CPU y memoria representan utilización de recursos asignados al servicio, no porcentajes por contenedor individual. Un servicio sin tareas activas puede no publicar puntos. Los huecos se conservan: no se convierten en cero ni se interpolan. Container Insights no reconstruye datos anteriores a su activación. Las consultas de CloudWatch pueden generar cargos según el uso y la configuración de AWS.

El inventario se consulta en cada interacción. Las series se mantienen únicamente en la sesión del navegador hasta cambiar los filtros o pulsar **Actualizar datos**; no hay refresco automático. La aplicación pagina inventarios y resultados de CloudWatch y divide consultas en lotes. El inventario, sus contadores y su CSV incluyen todos los servicios del clúster seleccionado. La lista desplegable permite elegir un solo servicio; las gráficas y el CSV de métricas corresponden únicamente a ese servicio.

## Permisos

El usuario/rol necesita estas acciones de lectura en cada cuenta (ejemplo para un administrador IAM):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["ecs:ListClusters", "ecs:ListServices", "ecs:DescribeServices", "rds:DescribeDBInstances", "elasticache:DescribeCacheClusters", "application-autoscaling:DescribeScalableTargets", "cloudwatch:GetMetricData"],
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

## Módulo RDS

El menú lateral ofrece enlaces a **ECS** y **RDS** (ruta `/rds`). En RDS selecciona perfil, región y una instancia de la lista desplegable para consultar CPU (%), memoria disponible (GiB) y conexiones. Incluye instancias Aurora listadas por RDS. Se pueden elegir 1, 7, 14 o 30 días, promedio o máximo por intervalo y descargar inventario y métricas CSV. Los datos históricos se conservan en la sesión del navegador hasta cambiar filtros o pulsar **Actualizar datos**.

Se consultan `CPUUtilization`, `FreeableMemory` y `DatabaseConnections` de `AWS/RDS` por `DBInstanceIdentifier`. La memoria representa RAM disponible, no porcentaje usado; el CSV conserva el valor original en bytes y su unidad. El resumen y la gráfica convierten memoria a GiB. Los puntos ausentes se conservan como huecos. Los valores de conexiones agregados por promedio pueden ser decimales. Este módulo requiere `rds:DescribeDBInstances` además de los permisos compartidos de CloudWatch y STS.

Referencia: [métricas oficiales de RDS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-metrics.html).

La tabla inicial de ECS incluye CPU de todos los servicios durante el histórico seleccionado. `cpu_mean` se calcula como suma de CPU dividida entre el número de muestras publicadas y `cpu_max` como el mayor máximo publicado, independientemente de la estadística elegida para las gráficas. Sin muestras, los campos quedan vacíos. El CSV de inventario incluye estas columnas y la ventana UTC. Cambiar el servicio de las gráficas no vuelve a consultar este resumen; cambiar el histórico o pulsar Actualizar sí. Estas métricas requieren consultas adicionales de CloudWatch para todos los servicios del clúster.

La tabla inicial de RDS incluye `cpu_mean` y `cpu_max` en porcentaje, y `memory_mean` y `memory_max` de memoria disponible en GiB para todas las instancias del inventario. Usa el histórico seleccionado; el promedio está ponderado por muestras y el máximo se calcula con los máximos publicados. La estadística elegida para las gráficas no cambia este resumen. Se conserva en la sesión hasta cambiar cuenta, región, inventario o histórico, o pulsar Actualizar. El CSV incluye estos valores, unidades y ventana UTC. Sin muestras los campos quedan vacíos. Se realizan consultas adicionales de CloudWatch para el inventario completo.

La tabla inicial de ECS muestra `tasks_min` y `tasks_max` junto a `desired`: son los límites actuales de Service Auto Scaling, consultados con `application-autoscaling:DescribeScalableTargets`. También se incluyen en el CSV. Sin un target configurado quedan vacíos; si falta permiso, se muestra un aviso. No son mínimos y máximos históricos. Referencia: [DescribeScalableTargets](https://docs.aws.amazon.com/autoscaling/application/APIReference/API_DescribeScalableTargets.html).

## Módulo ElastiCache

El menú incluye **ElastiCache** (`/elasticache`). Descubre todos los nodos de clústeres Redis OSS, Valkey y Memcached de la cuenta y región seleccionadas, con paginación. Esta versión no incluye cachés Serverless. Identifica cada nodo por clúster e ID de nodo para evitar mezclar series.

La tabla inicial muestra inventario y promedio ponderado por muestras y máximo de CPU (%), memoria disponible (GiB) y conexiones para el histórico seleccionado. Las columnas sin datos quedan vacías. El desplegable muestra las gráficas de un único nodo con `CPUUtilization`, `FreeableMemory` y `CurrConnections` de `AWS/ElastiCache`, usando las dimensiones `CacheClusterId` y `CacheNodeId`. CPU corresponde al host, no al hilo del motor, y memoria es RAM disponible del host, no porcentaje de memoria usada por los datos.

Permite 1, 7, 14 o 30 días, intervalos de 5, 15, 30 o 60 minutos y promedio o máximo por intervalo. Incluye resumen del período y CSV de inventario y métricas. El CSV de métricas conserva memoria en bytes; tabla y gráficas la muestran en GiB. Las fechas son UTC y los huecos no se rellenan con cero. Los resultados históricos se conservan en la sesión hasta cambiar filtros o pulsar Actualizar; cambiar de nodo no vuelve a consultar el resumen del inventario completo. Las consultas adicionales de CloudWatch dependen del número de nodos.

Requiere `elasticache:DescribeCacheClusters`, `cloudwatch:GetMetricData` y la verificación compartida con STS. No realiza conexiones a los endpoints de caché ni modifica recursos.

Referencias: [métricas del host](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/CacheMetrics.HostLevel.html), [métricas de Valkey y Redis OSS](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/CacheMetrics.Redis.html), [métricas de Memcached](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/serverless-metrics-events.memcached.html).
