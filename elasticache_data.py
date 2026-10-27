"""Inventario ElastiCache y métricas CloudWatch de solo lectura."""
from datetime import datetime, timedelta, timezone

import pandas as pd

from ecs_data import client, metric_history

METRICS = {"CPUUtilization": "%", "FreeableMemory": "Bytes", "CurrConnections": "Count"}


def nodes(session):
    rows = []
    for page in client(session, "elasticache").get_paginator("describe_cache_clusters").paginate(ShowCacheNodeInfo=True):
        for cluster in page["CacheClusters"]:
            for node in cluster.get("CacheNodes", []):
                rows.append({"node": f"{cluster['CacheClusterId']}/{node['CacheNodeId']}",
                             "cluster": cluster["CacheClusterId"], "node_id": node["CacheNodeId"],
                             "engine": cluster["Engine"], "class": cluster["CacheNodeType"],
                             "status": node["CacheNodeStatus"],
                             "replication_group": cluster.get("ReplicationGroupId", ""),
                             "availability_zone": node.get("CustomerAvailabilityZone", "")})
    return pd.DataFrame(rows, columns=["node", "cluster", "node_id", "engine", "class", "status",
                                       "replication_group", "availability_zone"]).sort_values("node")


def dimensions(name):
    cluster, node = name.split("/", 1)
    return [{"Name": "CacheClusterId", "Value": cluster}, {"Name": "CacheNodeId", "Value": node}]


def history(session, names, days, statistic="Average"):
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(days=days)
    period = {1: 300, 7: 900, 14: 1800, 30: 3600}[days]
    queries, lookup = [], {}
    for name in names:
        for metric in METRICS:
            key = f"m{len(queries)}"
            lookup[key] = (name, metric)
            queries.append({"Id": key, "MetricStat": {"Metric": {
                "Namespace": "AWS/ElastiCache", "MetricName": metric,
                "Dimensions": dimensions(name)},
                "Period": period, "Stat": statistic}, "ReturnData": True})
    frame, warnings, start, end, period = metric_history(session, queries, lookup, start, end, period)
    frame = frame.rename(columns={"service": "node"})
    frame["unit"] = frame.metric.map(METRICS)
    return frame, warnings, start, end, period


def utilization_summary(session, names, days):
    """CPU y memoria: promedio ponderado por muestras y máximo del período."""
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(days=days)
    period = {1: 300, 7: 900, 14: 1800, 30: 3600}[days]
    queries, lookup = [], {}
    for name in names:
        for metric in ('CPUUtilization', 'FreeableMemory', 'CurrConnections'):
            for statistic in ('Sum', 'SampleCount', 'Maximum'):
                key = f'm{len(queries)}'
                lookup[key] = (name, f'{metric}:{statistic}')
                queries.append({'Id': key, 'MetricStat': {'Metric': {
                    'Namespace': 'AWS/ElastiCache', 'MetricName': metric,
                    'Dimensions': dimensions(name)},
                    'Period': period, 'Stat': statistic}, 'ReturnData': True})
    frame, warnings, start, end, _ = metric_history(session, queries, lookup, start, end, period)
    rows = []
    for name in names:
        row = {'node': name}
        subset = frame[frame.service == name]
        for metric, prefix, divisor in [('CPUUtilization', 'cpu', 1),
                                         ('FreeableMemory', 'memory', 1024 ** 3),
                                         ('CurrConnections', 'connections', 1)]:
            sums = subset[subset.metric == f'{metric}:Sum'].set_index('timestamp').value
            counts = subset[subset.metric == f'{metric}:SampleCount'].set_index('timestamp').value
            paired = pd.concat([sums.rename('sum'), counts.rename('count')], axis=1).dropna()
            count = paired['count'].sum()
            row[f'{prefix}_mean'] = paired['sum'].sum() / count / divisor if count else float('nan')
            row[f'{prefix}_max'] = subset[subset.metric == f'{metric}:Maximum'].value.max() / divisor
        rows.append(row)
    return pd.DataFrame(rows, columns=['node', 'cpu_mean', 'cpu_max', 'memory_mean', 'memory_max', 'connections_mean', 'connections_max']), warnings, start, end
