"""Inventario RDS y métricas CloudWatch de solo lectura."""
from datetime import datetime, timedelta, timezone

import pandas as pd

from ecs_data import client, metric_history

METRICS = {"CPUUtilization": "%", "FreeableMemory": "Bytes", "DatabaseConnections": "Count"}


def instances(session):
    rows = []
    for page in client(session, "rds").get_paginator("describe_db_instances").paginate():
        for db in page["DBInstances"]:
            rows.append({"instance": db["DBInstanceIdentifier"], "engine": db["Engine"],
                         "class": db["DBInstanceClass"], "status": db["DBInstanceStatus"],
                         "cluster": db.get("DBClusterIdentifier", ""),
                         "availability_zone": db.get("AvailabilityZone", "")})
    return pd.DataFrame(rows, columns=["instance", "engine", "class", "status", "cluster",
                                       "availability_zone"]).sort_values("instance")


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
                "Namespace": "AWS/RDS", "MetricName": metric,
                "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": name}]},
                "Period": period, "Stat": statistic}, "ReturnData": True})
    frame, warnings, start, end, period = metric_history(session, queries, lookup, start, end, period)
    frame = frame.rename(columns={"service": "instance"})
    frame["unit"] = frame.metric.map(METRICS)
    return frame, warnings, start, end, period
