"""Ejecutar con: streamlit run app.py"""
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from botocore.exceptions import BotoCoreError, ClientError

import ecs_data as aws

st.set_page_config(page_title="AWS · Workloads", page_icon="📊", layout="wide")


def csv_download(frame, filename, label):
    st.download_button(label, frame.to_csv(index=False).encode("utf-8-sig"), filename,
                       "text/csv", key=filename)


def main():
    st.title("ECS · Comportamiento de microservicios")
    st.caption("Inventario actual de ECS y métricas históricas de CloudWatch · solo lectura")
    available = aws.profiles()
    if not available:
        st.warning("No hay perfiles AWS CLI. Configura uno con aws configure sso o aws configure.")
        return
    with st.sidebar:
        st.header("Conexión AWS")
        account = st.text_input("Filtrar por cuenta o perfil", placeholder="123456789012 o producción")
        matching = [name for name, number in available.items()
                    if account.lower() in name.lower() or account in number]
        if not matching:
            st.info("Sin perfiles coincidentes. La cuenta debe tener un perfil configurado localmente.")
            return
        profile = st.selectbox("Perfil", matching,
                               format_func=lambda name: f"{name} · {available[name] or 'cuenta por verificar'}")
        base = aws.session_for(profile, None)
        region = st.text_input("Región", value=base.region_name or "us-east-1").strip()
        if not region:
            st.info("Escribe una región AWS.")
            return
        days = st.selectbox("Histórico", [1, 7, 14, 30],
                            format_func=lambda n: {1: "1 día", 7: "1 semana", 14: "2 semanas", 30: "1 mes (30 días)"}[n])
        statistic = st.selectbox("Estadística por intervalo", ["Average", "Maximum"],
                                format_func=lambda s: {"Average": "Promedio", "Maximum": "Máximo"}[s])
        include_tasks = st.checkbox("Historial de tareas (Container Insights)")
        refresh = st.button("Actualizar datos", width="stretch")
        st.caption("Para SSO ejecuta en tu terminal: aws sso login --profile NOMBRE. Después actualiza los datos.")
    session = aws.session_for(profile, region)
    identity = aws.client(session, "sts").get_caller_identity()
    st.info(f"Cuenta verificada: {identity['Account']} · Perfil: {profile} · Región: {region}")
    cluster_arns = aws.clusters(session)
    if not cluster_arns:
        st.info("No hay clústeres ECS en esta cuenta y región.")
        return
    st.caption(f"Clústeres encontrados en esta cuenta y región: {len(cluster_arns)}")
    cluster = st.selectbox("Clúster", cluster_arns, format_func=lambda arn: arn.rsplit("/", 1)[-1])
    current = aws.services(session, cluster)
    if current.empty:
        st.info("El clúster no tiene servicios ECS.")
        return
    a, b, c, d = st.columns(4)
    a.metric("Servicios del clúster", len(current))
    b.metric("Tareas deseadas actuales", int(current.desired.sum()))
    c.metric("Tareas en ejecución actuales", int(current.running.sum()))
    d.metric("Tareas pendientes actuales", int(current.pending.sum()))
    st.caption(f"Inventario completo del clúster seleccionado · Consultado: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    cpu_key = (profile, identity["Account"], region, cluster, tuple(current.service), days)
    if refresh or st.session_state.get("cpu_summary_key") != cpu_key:
        with st.spinner("Consultando CPU de los servicios…"):
            result = aws.cpu_summary(session, cluster, current.service.tolist(), days)
        st.session_state.cpu_summary_result = result
        st.session_state.cpu_summary_key = cpu_key
    cpu, cpu_warnings, cpu_start, cpu_end = st.session_state.cpu_summary_result
    inventory = current.drop(columns=["task_definition"], errors="ignore").merge(cpu, on="service", how="left")
    try:
        limits = aws.task_limits(session, cluster, current.service.tolist())
    except ClientError as error:
        if error.response["Error"]["Code"] not in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation"):
            raise
        st.warning("No se pudieron consultar los límites de tareas. Se requiere el permiso "
                   "application-autoscaling:DescribeScalableTargets.")
        limits = pd.DataFrame(columns=["service", "tasks_min", "tasks_max"])
    inventory = inventory.merge(limits, on="service", how="left")
    for column in ("tasks_min", "tasks_max"):
        inventory[column] = inventory[column].astype("Int64")
    inventory = inventory[["service", "desired", "tasks_min", "tasks_max"] +
                          [column for column in inventory if column not in
                           ("service", "desired", "tasks_min", "tasks_max")]]
    st.caption("Mínimo y máximo de tareas: límites actuales de Auto Scaling; vacíos si no está configurado "
               "o no se pudo consultar. No representan valores históricos.")
    st.caption(f"CPU del histórico UTC: {cpu_start:%Y-%m-%d %H:%M} → {cpu_end:%Y-%m-%d %H:%M}. "
               "mean: promedio ponderado por muestras; máximo: mayor valor publicado. Sin datos se muestra vacío.")
    for warning in cpu_warnings:
        st.warning(warning)
    st.dataframe(inventory, hide_index=True, width="stretch", column_config={
        "tasks_min": st.column_config.NumberColumn("Tareas mínimo", format="%d"),
        "tasks_max": st.column_config.NumberColumn("Tareas máximo", format="%d"),
        "cpu_mean": st.column_config.NumberColumn("CPU mean (%)", format="%.2f"),
        "cpu_max": st.column_config.NumberColumn("CPU máximo (%)", format="%.2f"),
    })
    csv_download(inventory.assign(account=identity["Account"], region=region, cluster=cluster,
                                  cpu_start_utc=cpu_start, cpu_end_utc=cpu_end),
                 "ecs_inventory.csv", "Descargar inventario completo CSV")
    service = st.selectbox(
        "Servicio para mostrar en las gráficas", current.service.tolist(),
        key=f"metric_service:{profile}:{region}:{cluster}",
        help="El inventario y los contadores muestran todo el clúster; las gráficas muestran solo el servicio elegido.",
    )
    selected = [service]
    st.caption(f"Métricas del servicio: {service}")
    key = (profile, identity["Account"], region, cluster, tuple(selected), days, statistic, include_tasks)
    # Cache privado de esta sesión de navegador; nunca comparte datos entre usuarios.
    if refresh or st.session_state.get("history_key") != key:
        with st.spinner("Consultando métricas de CloudWatch…"):
            result = aws.history(session, cluster, selected, days, statistic, include_tasks)
        st.session_state.history_result = result
        st.session_state.history_key = key
    frame, warnings, start, end, period = st.session_state.history_result
    st.caption(f"Ventana UTC: {start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M} · "
               f"Intervalo: {period // 60} min · Estadística: {statistic}. Pulsa Actualizar para renovar el histórico.")
    for warning in warnings:
        st.warning(warning)
    labels = {"CPUUtilization": "CPU (%)", "MemoryUtilization": "Memoria (%)",
              "DesiredTaskCount": "Tareas deseadas (histórico)",
              "RunningTaskCount": "Tareas en ejecución (histórico)"}
    for metric, label in labels.items():
        if metric.endswith("TaskCount") and not include_tasks:
            continue
        st.subheader(label)
        subset = frame[frame.metric == metric]
        missing = sorted(set(selected) - set(subset.service))
        if missing:
            st.info("Sin puntos publicados para: " + ", ".join(missing))
        if not subset.empty:
            pivot = subset.pivot(index="timestamp", columns="service", values="value")
            # Reindexar evita dibujar continuidad a través de intervalos sin publicación.
            index = pd.date_range(pivot.index.min(), pivot.index.max(), freq=f"{period}s")
            st.line_chart(pivot.reindex(index), x_label="Fecha (UTC)", y_label=label)
    st.caption("CPU y memoria corresponden al servicio, agregadas sobre sus tareas. Los huecos no son cero. "
               "El historial de tareas requiere métricas de Container Insights publicadas en el período.")
    if not frame.empty:
        st.subheader("Resumen del período")
        utilization = frame[frame.metric.isin(["CPUUtilization", "MemoryUtilization"])]
        if not utilization.empty:
            summary = utilization.groupby(["service", "metric"]).value.agg(["mean", "max", "count"]).reset_index()
            st.dataframe(summary, hide_index=True, width="stretch")
            st.caption("mean y max resumen los puntos agregados consultados; count cuenta puntos disponibles.")
        csv_download(frame.assign(account=identity["Account"], region=region, cluster=cluster,
                                  statistic=statistic, period_seconds=period),
                     "ecs_metrics.csv", "Descargar métricas CSV")


if __name__ == "__main__":
    try:
        page = st.navigation([
            st.Page(main, title="ECS", icon=":material/apps:", default=True),
            st.Page("app_pages/rds.py", title="RDS", icon=":material/database:", url_path="rds"),
            st.Page("app_pages/elasticache.py", title="ElastiCache", icon=":material/memory:", url_path="elasticache"),
        ])
        page.run()
    except (BotoCoreError, ClientError, RuntimeError, ValueError) as error:
        st.error(f"No fue posible completar la consulta: {error}")
        st.info("Revisa la sesión SSO, la conectividad, el perfil, la región y los permisos IAM. "
                "Si usas SSO, ejecuta aws sso login --profile NOMBRE y vuelve a actualizar.")
