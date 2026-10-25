import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import rds_data


class RdsTests(unittest.TestCase):
    def test_inventory_pages_and_aurora(self):
        client = Mock()
        client.get_paginator.return_value.paginate.return_value = [
            {'DBInstances': [dict(DBInstanceIdentifier='db-b', Engine='mysql',
                                 DBInstanceClass='db.t3.micro', DBInstanceStatus='available')]},
            {'DBInstances': [dict(DBInstanceIdentifier='db-a', Engine='aurora-postgresql',
                                 DBInstanceClass='db.r6g.large', DBInstanceStatus='available',
                                 DBClusterIdentifier='cluster-a')]}]
        with patch('rds_data.client', return_value=client):
            result = rds_data.instances(None)
        self.assertEqual(result.instance.tolist(), ['db-a', 'db-b'])
        self.assertEqual(result.iloc[0]['cluster'], 'cluster-a')
        client.get_paginator.assert_called_once_with('describe_db_instances')

    def test_dimensions_pagination_and_units(self):
        client = Mock()
        now = datetime.now(timezone.utc)
        client.get_metric_data.side_effect = [
            {'MetricDataResults': [], 'NextToken': 'next'},
            {'MetricDataResults': [dict(Id='m1', Timestamps=[now], Values=[1073741824],
                                       StatusCode='Complete')]}]
        with patch('ecs_data.client', return_value=client):
            frame, warnings, start, end, period = rds_data.history(None, ['db-a'], 14, 'Maximum')
        query = client.get_metric_data.call_args_list[0].kwargs['MetricDataQueries']
        self.assertEqual(len(query), 3)
        for item in query:
            metric = item['MetricStat']
            self.assertEqual(metric['Stat'], 'Maximum')
            self.assertEqual(metric['Metric']['Namespace'], 'AWS/RDS')
            self.assertEqual(metric['Metric']['Dimensions'], [{'Name': 'DBInstanceIdentifier', 'Value': 'db-a'}])
        self.assertEqual(client.get_metric_data.call_args.kwargs['NextToken'], 'next')
        self.assertEqual(frame.iloc[0]['unit'], 'Bytes')
        self.assertEqual(frame.iloc[0]['instance'], 'db-a')
        self.assertEqual(period, 1800)
        self.assertEqual((end-start).days, 14)
        self.assertFalse(warnings)
