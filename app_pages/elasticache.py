"""Métricas históricas por nodo ElastiCache."""
import pandas as pd
import streamlit as st

import ecs_data as aws
import elasticache_data as cache

st.title("ElastiCache · Comportamiento de cachés")
st.caption("Clústeres con nodos Redis OSS, Valkey y Memcached · solo lectura. No incluye cachés Serverless.")
available = aws.profiles()
if not available:
    st.warning("No hay perfiles AWS CLI. Configura uno con aws configure sso o aws configure.")
    st.stop()

with st.sidebar:
    st.header("Conexión AWS")
    account = st.text_input("Filtrar por cuenta o perfil", key="cache_account")
    matching = [name for name, number in available.items()
                if account.lower() in name.lower() or account in number]
    if not matching:
        st.info("Sin perfiles coincidentes.")
        st.stop()
    profile = st.selectbox("Perfil", matching, key="cache_profile",
                           format_func=lambda name: f"{name} · {available[name] or 'cuenta por verificar'}")
    base = aws.session_for(profile, None)
    region = st.text_input("Región", value=base.region_name or "us-east-1", key="cache_region").strip()
    if not region:
        st.info("Escribe una región AWS.")
        st.stop()
    days = st.selectbox("Histórico", [1, 7, 14, 30], key="cache_days",
                        format_func=lambda n: {1: "1 día", 7: "1 semana", 14: "2 semanas", 30: "1 mes (30 días)"}[n])
    statistic = st.selectbox("Estadística por intervalo", ["Average", "Maximum"], key="cache_statistic",
                            format_func=lambda s: {"Average": "Promedio", "Maximum": "Máximo"}[s])
    refresh = st.button("Actualizar datos", width="stretch", key="cache_refresh")
    st.caption("Para SSO ejecuta: aws sso login --profile NOMBRE. Después actualiza los datos.")

session = aws.session_for(profile, region)
identity = aws.client(session, "sts").get_caller_identity()
st.info(f"Cuenta verificada: {identity['Account']} · Perfil: {profile} · Región: {region}")
current = cache.nodes(session)
if current.empty:
    st.info("No hay nodos ElastiCache en esta cuenta y región.")
    st.stop()
st.metric("Nodos ElastiCache", len(current))
summary_key = (profile, identity["Account"], region, tuple(current.node), days)
if refresh or st.session_state.get("cache_summary_key") != summary_key:
    with st.spinner("Consultando CPU y memoria de los nodos…"):
        result = cache.utilization_summary(session, current.node.tolist(), days)
    st.session_state.cache_summary_result = result
    st.session_state.cache_summary_key = summary_key
utilization, summary_warnings, summary_start, summary_end = st.session_state.cache_summary_result
inventory = current.merge(utilization, on="node", how="left")
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
    "connections_mean": st.column_config.NumberColumn("Conexiones mean", format="%.2f"),
    "connections_max": st.column_config.NumberColumn("Conexiones máximo", format="%.0f"),
})
st.download_button("Descargar inventario completo CSV",
                   inventory.assign(account=identity["Account"], region=region,
                                    summary_start_utc=summary_start, summary_end_utc=summary_end,
                                    memory_unit="GiB", cpu_unit="%").to_csv(index=False).encode("utf-8-sig"),
                   "cache_inventory.csv", "text/csv")
instance = st.selectbox("Nodo para mostrar en las gráficas", current.node.tolist(),
                        key=f"cache_instance:{profile}:{region}")
selected = [instance]
st.caption(f"Métricas del nodo: {instance}")
key = (profile, identity["Account"], region, tuple(selected), days, statistic)
if refresh or st.session_state.get("cache_history_key") != key:
    with st.spinner("Consultando métricas de CloudWatch…"):
        result = cache.history(session, selected, days, statistic)
    st.session_state.cache_history_result = result
    st.session_state.cache_history_key = key
frame, warnings, start, end, period = st.session_state.cache_history_result
st.caption(f"Ventana UTC: {start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M} · "
           f"Intervalo: {period // 60} min · Estadística: {statistic}. Pulsa Actualizar para renovar el histórico.")
for warning in warnings:
    st.warning(warning)
labels = {"CPUUtilization": "CPU (%)", "FreeableMemory": "Memoria disponible (GiB)",
          "CurrConnections": "Conexiones"}
display = frame.copy()
display.loc[display.metric == "FreeableMemory", "value"] /= 1024 ** 3
display.loc[display.metric == "FreeableMemory", "unit"] = "GiB"
for metric, label in labels.items():
    st.subheader(label)
    subset = display[display.metric == metric]
    missing = sorted(set(selected) - set(subset.node))
    if missing:
        st.info("Sin puntos publicados para: " + ", ".join(missing))
    if not subset.empty:
        pivot = subset.pivot(index="timestamp", columns="node", values="value")
        index = pd.date_range(pivot.index.min(), pivot.index.max(), freq=f"{period}s")
        st.line_chart(pivot.reindex(index), x_label="Fecha (UTC)", y_label=label)
st.caption("FreeableMemory indica memoria disponible, no porcentaje de uso. Las conexiones corresponden "
           "a CurrConnections por nodo. Los huecos no son cero. CPU y memoria corresponden al host del nodo.")
if not frame.empty:
    st.subheader("Resumen del período")
    summary = display.groupby(["node", "metric", "unit"]).value.agg(["mean", "max", "count"]).reset_index()
    st.dataframe(summary, hide_index=True, width="stretch")
    st.caption("mean y max resumen los puntos agregados consultados; count cuenta puntos disponibles.")
    st.download_button("Descargar métricas CSV", frame.assign(account=identity["Account"], region=region,
                       statistic=statistic, period_seconds=period).to_csv(index=False).encode("utf-8-sig"),
                       "cache_metrics.csv", "text/csv")
