import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from botocore.stub import Stubber, ANY
import boto3

import ecs_data


class MetricsTests(unittest.TestCase):
    def test_pagination_missing_points_and_dimensions(self):
        cw = boto3.client('cloudwatch', region_name='us-east-1',
                          aws_access_key_id='testing', aws_secret_access_key='testing')
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        params = {'MetricDataQueries': ANY, 'StartTime': ANY, 'EndTime': ANY,
                  'ScanBy': 'TimestampAscending'}
        with Stubber(cw) as stub:
            stub.add_response('get_metric_data', {'MetricDataResults': [
                {'Id': 'm0', 'Timestamps': [timestamp], 'Values': [42.0], 'StatusCode': 'Complete'},
                {'Id': 'm1', 'Timestamps': [], 'Values': [], 'StatusCode': 'Complete'}],
                'NextToken': 'next'}, params)
            stub.add_response('get_metric_data', {'MetricDataResults': [
                {'Id': 'm0', 'Timestamps': [timestamp], 'Values': [42.0], 'StatusCode': 'Complete'}]},
                dict(params, NextToken='next'))
            with patch('ecs_data.client', return_value=cw):
                frame, warnings, start, end, period = ecs_data.history(
                    None, 'arn:aws:ecs:us-east-1:123456789012:cluster/demo', ['api'], 30)
            stub.assert_no_pending_responses()
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]['value'], 42)
        self.assertEqual(frame.iloc[0]['metric'], 'CPUUtilization')
        self.assertEqual(period, 3600)
        self.assertEqual((end - start).days, 30)
        self.assertFalse(warnings)

    def test_query_batching_and_partial_results(self):
        cw = Mock()
        cw.get_metric_data.return_value = {'MetricDataResults': [
            {'Id': 'm0', 'StatusCode': 'PartialData', 'Timestamps': [], 'Values': []}]}
        with patch('ecs_data.client', return_value=cw):
            frame, warnings, *_ = ecs_data.history(None, 'demo', [f's{i}' for i in range(26)],
                                                   7, include_tasks=True)
        self.assertTrue(frame.empty)
        self.assertEqual(cw.get_metric_data.call_count, 2)
        first = cw.get_metric_data.call_args_list[0].kwargs['MetricDataQueries']
        second = cw.get_metric_data.call_args_list[1].kwargs['MetricDataQueries']
        self.assertEqual((len(first), len(second)), (100, 4))
        self.assertEqual(first[0]['MetricStat']['Metric']['Dimensions'][0]['Value'], 'demo')
        self.assertEqual(first[2]['MetricStat']['Metric']['Namespace'], 'ECS/ContainerInsights')
        self.assertTrue(warnings)

    def test_describe_services_batches_and_failures(self):
        ecs = Mock()
        ecs.get_paginator.return_value.paginate.return_value = [
            {'serviceArns': [f's{i}' for i in range(12)]}]
        ecs.describe_services.side_effect = [
            {'services': [], 'failures': []}, {'services': [], 'failures': [{'reason': 'MISSING'}]}]
        with patch('ecs_data.client', return_value=ecs):
            with self.assertRaises(RuntimeError):
                ecs_data.services(None, 'demo')
        self.assertEqual(len(ecs.describe_services.call_args_list[0].kwargs['services']), 10)
        self.assertEqual(len(ecs.describe_services.call_args_list[1].kwargs['services']), 2)

    def test_all_service_pages_are_described_for_large_clusters(self):
        for count in (68, 34):
            with self.subTest(count=count):
                ecs = Mock()
                names = [f'api-{i:02}' for i in range(count)]
                ecs.get_paginator.return_value.paginate.return_value = [
                    {'serviceArns': names[i:i + 10]} for i in range(0, count, 10)]
                ecs.describe_services.side_effect = lambda **kwargs: {
                    'services': [dict(serviceName=name, desiredCount=2, runningCount=1,
                                      pendingCount=1, status='ACTIVE', taskDefinition='demo:1')
                                 for name in kwargs['services']]}
                with patch('ecs_data.client', return_value=ecs):
                    result = ecs_data.services(None, 'prod')
                self.assertEqual(len(result), count)
                self.assertEqual(set(result.service), set(names))
                self.assertEqual(result.desired.sum(), count * 2)
                self.assertEqual(ecs.describe_services.call_count, (count + 9) // 10)
                ecs.get_paginator.return_value.paginate.assert_called_once_with(cluster='prod')


if __name__ == '__main__':
    unittest.main()
