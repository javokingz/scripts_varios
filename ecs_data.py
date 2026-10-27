"""Lecturas de ECS y CloudWatch; nunca modifica recursos AWS."""
from datetime import datetime, timedelta, timezone

import boto3
import pandas as pd
from botocore.config import Config

CONFIG = Config(retries={"mode": "standard", "max_attempts": 4}, connect_timeout=10, read_timeout=30)
METRICS = {
    "CPUUtilization": "AWS/ECS",
    "MemoryUtilization": "AWS/ECS",
    "DesiredTaskCount": "ECS/ContainerInsights",
    "RunningTaskCount": "ECS/ContainerInsights",
}
COLUMNS = ["timestamp", "service", "metric", "value"]


def profiles():
    session = boto3.Session()
    # scoped configuration contains profile metadata, never resolve/export credentials.
    config = session._session.full_config.get("profiles", {})
    result = {}
    for name in session.available_profiles:
        entry = config.get(name, {})
        account = entry.get("sso_account_id", "")
        role = entry.get("role_arn", "").split(":")
        if not account and len(role) > 4:
            account = role[4]
        result[name] = account
    return result


def session_for(profile, region):
    return boto3.Session(profile_name=profile, region_name=region)


def client(session, service):
    return session.client(service, config=CONFIG)


def clusters(session):
    return sorted(arn for page in client(session, "ecs").get_paginator("list_clusters").paginate()
                  for arn in page["clusterArns"])


def services(session, cluster):
    ecs = client(session, "ecs")
    arns = [arn for page in ecs.get_paginator("list_services").paginate(cluster=cluster)
            for arn in page["serviceArns"]]
    rows = []
    for offset in range(0, len(arns), 10):
        response = ecs.describe_services(cluster=cluster, services=arns[offset:offset + 10])
        if response.get("failures"):
            raise RuntimeError("No se pudieron describir algunos servicios; vuelve a actualizar.")
        for service in response["services"]:
            rows.append({"service": service["serviceName"], "desired": service["desiredCount"],
                         "running": service["runningCount"], "pending": service["pendingCount"],
                         "status": service["status"], "task_definition": service["taskDefinition"]})
    return pd.DataFrame(rows, columns=["service", "desired", "running", "pending", "status", "task_definition"]).sort_values("service")


def task_limits(session, cluster, names):
    """Límites actuales de Service Auto Scaling; ausentes si no hay target."""
    scaling = client(session, "application-autoscaling")
    cluster_name = cluster.rsplit("/", 1)[-1]
    resources = {f"service/{cluster_name}/{name}": name for name in names}
    rows = []
    identifiers = list(resources)
    for offset in range(0, len(identifiers), 50):
        params = dict(ServiceNamespace="ecs", ScalableDimension="ecs:service:DesiredCount",
                      ResourceIds=identifiers[offset:offset + 50])
        while True:
            response = scaling.describe_scalable_targets(**params)
            for target in response.get("ScalableTargets", []):
                name = resources.get(target["ResourceId"])
                if name is not None:
                    rows.append({"service": name, "tasks_min": target["MinCapacity"],
                                 "tasks_max": target["MaxCapacity"]})
            if not response.get("NextToken"):
                break
            params["NextToken"] = response["NextToken"]
    return pd.DataFrame(rows, columns=["service", "tasks_min", "tasks_max"])


def history(session, cluster, names, days, statistic="Average", include_tasks=False):
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(days=days)
    period = {1: 300, 7: 900, 14: 1800, 30: 3600}[days]
    queries, lookup = [], {}
    for name in names:
        for metric, namespace in METRICS.items():
            if namespace == "ECS/ContainerInsights" and not include_tasks:
                continue
            key = f"m{len(queries)}"
            lookup[key] = (name, metric)
            queries.append({"Id": key, "MetricStat": {"Metric": {
                "Namespace": namespace, "MetricName": metric,
                "Dimensions": [{"Name": "ClusterName", "Value": cluster.rsplit("/", 1)[-1]},
                               {"Name": "ServiceName", "Value": name}]},
                "Period": period, "Stat": statistic}, "ReturnData": True})
    return metric_history(session, queries, lookup, start, end, period)


def cpu_summary(session, cluster, names, days):
    """Promedio ponderado por muestras y máximo real de CPU en la ventana."""
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(days=days)
    period = {1: 300, 7: 900, 14: 1800, 30: 3600}[days]
    queries, lookup = [], {}
    for name in names:
        for statistic in ("Sum", "SampleCount", "Maximum"):
            key = f"m{len(queries)}"
            lookup[key] = (name, statistic)
            queries.append({"Id": key, "MetricStat": {"Metric": {
                "Namespace": "AWS/ECS", "MetricName": "CPUUtilization",
                "Dimensions": [{"Name": "ClusterName", "Value": cluster.rsplit("/", 1)[-1]},
                               {"Name": "ServiceName", "Value": name}]},
                "Period": period, "Stat": statistic}, "ReturnData": True})
    frame, warnings, start, end, _ = metric_history(session, queries, lookup, start, end, period)
    rows = []
    for name in names:
        subset = frame[frame.service == name]
        sums = subset[subset.metric == "Sum"].set_index("timestamp").value
        counts = subset[subset.metric == "SampleCount"].set_index("timestamp").value
        paired = pd.concat([sums.rename("sum"), counts.rename("count")], axis=1).dropna()
        count = paired["count"].sum()
        rows.append({"service": name,
                     "cpu_mean": paired["sum"].sum() / count if count else float("nan"),
                     "cpu_max": subset[subset.metric == "Maximum"].value.max()})
    return pd.DataFrame(rows, columns=["service", "cpu_mean", "cpu_max"]), warnings, start, end


def metric_history(session, queries, lookup, start, end, period):
    """Consulta series paginadas; conserva huecos y avisos de CloudWatch."""
    rows, warnings = [], []
    cw = client(session, "cloudwatch")
    for offset in range(0, len(queries), 100):
        params = dict(MetricDataQueries=queries[offset:offset + 100], StartTime=start,
                      EndTime=end, ScanBy="TimestampAscending")
        while True:
            response = cw.get_metric_data(**params)
            warnings.extend(message.get("Value", "") for message in response.get("Messages", []))
            for result in response.get("MetricDataResults", []):
                name, metric = lookup[result["Id"]]
                if result.get("StatusCode") not in (None, "Complete"):
                    warnings.append(f"{name} / {metric}: {result['StatusCode']}")
                warnings.extend(m.get("Value", "") for m in result.get("Messages", []))
                rows.extend({"timestamp": timestamp, "service": name, "metric": metric, "value": value}
                            for timestamp, value in zip(result.get("Timestamps", []), result.get("Values", [])))
            if not response.get("NextToken"):
                break
            params["NextToken"] = response["NextToken"]
    frame = pd.DataFrame(rows, columns=COLUMNS)
    if not frame.empty:
        frame = frame.drop_duplicates(["timestamp", "service", "metric"]).sort_values("timestamp")
    return frame, sorted(set(warnings)), start, end, period
