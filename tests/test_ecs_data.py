import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from botocore.stub import Stubber, ANY
import boto3

import ecs_data


class MetricsTests(unittest.TestCase):
    def test_task_limits_batches_pages_and_zero(self):
        scaling = Mock()
        scaling.describe_scalable_targets.side_effect = [
            {'ScalableTargets': [{'ResourceId': 'service/prod/api-0', 'MinCapacity': 0, 'MaxCapacity': 10}],
             'NextToken': 'next'},
            {'ScalableTargets': []},
            {'ScalableTargets': [{'ResourceId': 'service/prod/api-50', 'MinCapacity': 2, 'MaxCapacity': 20}]}]
        with patch('ecs_data.client', return_value=scaling):
            result = ecs_data.task_limits(None, 'arn:aws:ecs:us-east-1:123:cluster/prod',
                                           [f'api-{i}' for i in range(51)])
        calls = scaling.describe_scalable_targets.call_args_list
        self.assertEqual(len(calls[0].kwargs['ResourceIds']), 50)
        self.assertEqual(calls[1].kwargs['NextToken'], 'next')
        self.assertEqual(calls[2].kwargs['ResourceIds'], ['service/prod/api-50'])
        self.assertEqual(result.tasks_min.tolist(), [0, 2])
        self.assertEqual(result.tasks_max.tolist(), [10, 20])
        with patch('ecs_data.client', return_value=Mock(
                describe_scalable_targets=Mock(return_value={'ScalableTargets': []}))):
            self.assertTrue(ecs_data.task_limits(None, 'prod', ['manual']).empty)

    def test_cpu_summary_weighted_mean_max_and_missing(self):
        cw = Mock()
        first = datetime(2026, 1, 1, tzinfo=timezone.utc)
        second = datetime(2026, 1, 2, tzinfo=timezone.utc)
        cw.get_metric_data.return_value = {'MetricDataResults': [
            dict(Id='m0', Timestamps=[first, second], Values=[20, 180]),
            dict(Id='m1', Timestamps=[first, second], Values=[2, 3]),
            dict(Id='m2', Timestamps=[first, second], Values=[15, 90])]}
        with patch('ecs_data.client', return_value=cw):
            result, warnings, start, end = ecs_data.cpu_summary(None, 'demo', ['api', 'empty'], 7)
        self.assertEqual(result.iloc[0].cpu_mean, 40)
        self.assertEqual(result.iloc[0].cpu_max, 90)
        self.assertTrue(result.iloc[1][['cpu_mean', 'cpu_max']].isna().all())
        queries = cw.get_metric_data.call_args.kwargs['MetricDataQueries']
        self.assertEqual([q['MetricStat']['Stat'] for q in queries[:3]],
                         ['Sum', 'SampleCount', 'Maximum'])
        self.assertEqual((end-start).days, 7)

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
