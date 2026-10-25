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
    st.dataframe(current, hide_index=True, width="stretch")
    csv_download(current.assign(account=identity["Account"], region=region, cluster=cluster),
                 "ecs_inventory.csv", "Descargar inventario completo CSV")
    selected = st.multiselect(
        "Microservicios para comparar en las gráficas", current.service.tolist(),
        default=current.service.tolist(),
        key=f"metric_services:{profile}:{region}:{cluster}",
        help="Esta selección solo afecta las métricas históricas; el inventario y los contadores muestran todo el clúster.",
    )
    st.caption(f"Servicios seleccionados para métricas: {len(selected)} de {len(current)}")
    if not selected:
        st.info("Selecciona al menos un servicio para consultar las métricas históricas.")
        return
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
        ])
        page.run()
    except (BotoCoreError, ClientError, RuntimeError, ValueError) as error:
        st.error(f"No fue posible completar la consulta: {error}")
        st.info("Revisa la sesión SSO, la conectividad, el perfil, la región y los permisos IAM. "
                "Si usas SSO, ejecuta aws sso login --profile NOMBRE y vuelve a actualizar.")
