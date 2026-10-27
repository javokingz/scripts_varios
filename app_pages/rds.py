"""Métricas históricas por instancia RDS."""
import pandas as pd
import streamlit as st

import ecs_data as aws
import rds_data as rds

st.title("RDS · Comportamiento de bases de datos")
st.caption("Inventario actual de RDS y métricas históricas de CloudWatch · solo lectura")
available = aws.profiles()
if not available:
    st.warning("No hay perfiles AWS CLI. Configura uno con aws configure sso o aws configure.")
    st.stop()

with st.sidebar:
    st.header("Conexión AWS")
    account = st.text_input("Filtrar por cuenta o perfil", key="rds_account")
    matching = [name for name, number in available.items()
                if account.lower() in name.lower() or account in number]
    if not matching:
        st.info("Sin perfiles coincidentes.")
        st.stop()
    profile = st.selectbox("Perfil", matching, key="rds_profile",
                           format_func=lambda name: f"{name} · {available[name] or 'cuenta por verificar'}")
    base = aws.session_for(profile, None)
    region = st.text_input("Región", value=base.region_name or "us-east-1", key="rds_region").strip()
    if not region:
        st.info("Escribe una región AWS.")
        st.stop()
    days = st.selectbox("Histórico", [1, 7, 14, 30], key="rds_days",
                        format_func=lambda n: {1: "1 día", 7: "1 semana", 14: "2 semanas", 30: "1 mes (30 días)"}[n])
    statistic = st.selectbox("Estadística por intervalo", ["Average", "Maximum"], key="rds_statistic",
                            format_func=lambda s: {"Average": "Promedio", "Maximum": "Máximo"}[s])
    refresh = st.button("Actualizar datos", width="stretch", key="rds_refresh")
    st.caption("Para SSO ejecuta: aws sso login --profile NOMBRE. Después actualiza los datos.")

session = aws.session_for(profile, region)
identity = aws.client(session, "sts").get_caller_identity()
st.info(f"Cuenta verificada: {identity['Account']} · Perfil: {profile} · Región: {region}")
current = rds.instances(session)
if current.empty:
    st.info("No hay instancias RDS en esta cuenta y región.")
    st.stop()
st.metric("Instancias RDS", len(current))
summary_key = (profile, identity["Account"], region, tuple(current.instance), days)
if refresh or st.session_state.get("rds_summary_key") != summary_key:
    with st.spinner("Consultando CPU y memoria de las instancias…"):
        result = rds.utilization_summary(session, current.instance.tolist(), days)
    st.session_state.rds_summary_result = result
    st.session_state.rds_summary_key = summary_key
utilization, summary_warnings, summary_start, summary_end = st.session_state.rds_summary_result
inventory = current.merge(utilization, on="instance", how="left")
st.caption(f"Histórico UTC: {summary_start:%Y-%m-%d %H:%M} → {summary_end:%Y-%m-%d %H:%M}. "
           "mean: promedio ponderado por muestras; máximo: mayor valor publicado. "
           "La memoria es disponible (GiB), no memoria usada. Sin datos se muestra vacío.")
for warning in summary_warnings:
    st.warning(warning)
st.dataframe(inventory, hide_index=True, width="stretch", column_config={
    "cpu_mean": st.column_config.NumberColumn("CPU mean (%)", format="%.2f"),
    "cpu_max": st.column_config.NumberColumn("CPU máximo (%)", format="%.2f"),
    "memory_mean": st.column_config.NumberColumn("Memoria disponible mean (GiB)", format="%.2f"),
    "memory_max": st.column_config.NumberColumn("Memoria disponible máximo (GiB)", format="%.2f"),
})
st.download_button("Descargar inventario completo CSV",
                   inventory.assign(account=identity["Account"], region=region,
                                    summary_start_utc=summary_start, summary_end_utc=summary_end,
                                    memory_unit="GiB", cpu_unit="%").to_csv(index=False).encode("utf-8-sig"),
                   "rds_inventory.csv", "text/csv")
instance = st.selectbox("Instancia para mostrar en las gráficas", current.instance.tolist(),
                        key=f"rds_instance:{profile}:{region}")
selected = [instance]
st.caption(f"Métricas de la instancia: {instance}")
key = (profile, identity["Account"], region, tuple(selected), days, statistic)
if refresh or st.session_state.get("rds_history_key") != key:
    with st.spinner("Consultando métricas de CloudWatch…"):
        result = rds.history(session, selected, days, statistic)
    st.session_state.rds_history_result = result
    st.session_state.rds_history_key = key
frame, warnings, start, end, period = st.session_state.rds_history_result
st.caption(f"Ventana UTC: {start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M} · "
           f"Intervalo: {period // 60} min · Estadística: {statistic}. Pulsa Actualizar para renovar el histórico.")
for warning in warnings:
    st.warning(warning)
labels = {"CPUUtilization": "CPU (%)", "FreeableMemory": "Memoria disponible (GiB)",
          "DatabaseConnections": "Conexiones"}
display = frame.copy()
display.loc[display.metric == "FreeableMemory", "value"] /= 1024 ** 3
display.loc[display.metric == "FreeableMemory", "unit"] = "GiB"
for metric, label in labels.items():
    st.subheader(label)
    subset = display[display.metric == metric]
    missing = sorted(set(selected) - set(subset.instance))
    if missing:
        st.info("Sin puntos publicados para: " + ", ".join(missing))
    if not subset.empty:
        pivot = subset.pivot(index="timestamp", columns="instance", values="value")
        index = pd.date_range(pivot.index.min(), pivot.index.max(), freq=f"{period}s")
        st.line_chart(pivot.reindex(index), x_label="Fecha (UTC)", y_label=label)
st.caption("FreeableMemory indica memoria disponible, no porcentaje de uso. Las conexiones corresponden "
           "a DatabaseConnections por instancia. Los huecos no son cero. Incluye instancias de Aurora cuando existan.")
if not frame.empty:
    st.subheader("Resumen del período")
    summary = display.groupby(["instance", "metric", "unit"]).value.agg(["mean", "max", "count"]).reset_index()
    st.dataframe(summary, hide_index=True, width="stretch")
    st.caption("mean y max resumen los puntos agregados consultados; count cuenta puntos disponibles.")
    st.download_button("Descargar métricas CSV", frame.assign(account=identity["Account"], region=region,
                       statistic=statistic, period_seconds=period).to_csv(index=False).encode("utf-8-sig"),
                       "rds_metrics.csv", "text/csv")
