import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import elasticache_data


class ElastiCacheTests(unittest.TestCase):
    def test_inventory_pagination_and_node_identity(self):
        client = Mock()
        client.get_paginator.return_value.paginate.return_value = [
            {'CacheClusters': [dict(CacheClusterId='redis-a', Engine='redis', CacheNodeType='cache.t3.micro',
                CacheNodes=[dict(CacheNodeId='0001', CacheNodeStatus='available')])]},
            {'CacheClusters': [dict(CacheClusterId='memcached-b', Engine='memcached', CacheNodeType='cache.t3.micro',
                CacheNodes=[dict(CacheNodeId='0001', CacheNodeStatus='available'),
                            dict(CacheNodeId='0002', CacheNodeStatus='available')])]}]
        with patch('elasticache_data.client', return_value=client):
            result = elasticache_data.nodes(None)
        self.assertEqual(result.node.tolist(), ['memcached-b/0001', 'memcached-b/0002', 'redis-a/0001'])
        client.get_paginator.return_value.paginate.assert_called_once_with(ShowCacheNodeInfo=True)

    def test_dimensions_pagination_and_units(self):
        client = Mock()
        now = datetime.now(timezone.utc)
        client.get_metric_data.side_effect = [
            {'MetricDataResults': [], 'NextToken': 'next'},
            {'MetricDataResults': [dict(Id='m1', Timestamps=[now], Values=[1073741824],
                                       StatusCode='Complete')]}]
        with patch('ecs_data.client', return_value=client):
            frame, warnings, start, end, period = elasticache_data.history(None, ['redis-a/0001'], 14, 'Maximum')
        query = client.get_metric_data.call_args_list[0].kwargs['MetricDataQueries']
        self.assertEqual(len(query), 3)
        for item in query:
            metric = item['MetricStat']
            self.assertEqual(metric['Stat'], 'Maximum')
            self.assertEqual(metric['Metric']['Namespace'], 'AWS/ElastiCache')
            self.assertEqual(metric['Metric']['Dimensions'], [{'Name': 'CacheClusterId', 'Value': 'redis-a'}, {'Name': 'CacheNodeId', 'Value': '0001'}])
        self.assertEqual(client.get_metric_data.call_args.kwargs['NextToken'], 'next')
        self.assertEqual(frame.iloc[0]['unit'], 'Bytes')
        self.assertEqual(frame.iloc[0]['node'], 'redis-a/0001')
        self.assertEqual(period, 1800)
        self.assertEqual((end-start).days, 14)
        self.assertFalse(warnings)

    def test_summary_weighted_mean_and_memory_conversion(self):
        client = Mock()
        timestamps = [datetime(2026, 1, day, tzinfo=timezone.utc) for day in (1, 2)]
        client.get_metric_data.return_value = {'MetricDataResults': [
            dict(Id=f'm{i}', Timestamps=timestamps, Values=values)
            for i, values in enumerate(([20, 180], [2, 3], [15, 90],
                                         [2 * 1024**3, 6 * 1024**3], [2, 3],
                                         [1024**3, 3 * 1024**3]))]}
        with patch('ecs_data.client', return_value=client):
            frame, warnings, start, end = elasticache_data.utilization_summary(None, ['redis-a/0001', 'empty/0001'], 7)
        self.assertEqual(frame.iloc[0].cpu_mean, 40)
        self.assertEqual(frame.iloc[0].cpu_max, 90)
        self.assertEqual(frame.iloc[0].memory_mean, 1.6)
        self.assertEqual(frame.iloc[0].memory_max, 3)
        self.assertTrue(frame.iloc[1].drop('node').isna().all())
        queries = client.get_metric_data.call_args.kwargs['MetricDataQueries']
        self.assertEqual(len(queries), 18)
        self.assertEqual(queries[3]['MetricStat']['Metric']['MetricName'], 'FreeableMemory')
        self.assertEqual(queries[0]['MetricStat']['Metric']['Dimensions'],
                         [{'Name': 'CacheClusterId', 'Value': 'redis-a'}, {'Name': 'CacheNodeId', 'Value': '0001'}])
