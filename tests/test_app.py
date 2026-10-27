import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
from streamlit.testing.v1 import AppTest


class DashboardTests(unittest.TestCase):
    def setUp(self):
        now = datetime.now(timezone.utc)
        def summary(session, cluster, names, days):
            return (pd.DataFrame([dict(service=name, cpu_mean=12.5, cpu_max=80.0)
                                  for name in names]), [], now, now)
        patcher = patch('ecs_data.cpu_summary', side_effect=summary)
        self.cpu_summary = patcher.start()
        self.addCleanup(patcher.stop)
        limits = patch('ecs_data.task_limits', side_effect=lambda session, cluster, names:
                       pd.DataFrame([dict(service=name, tasks_min=1, tasks_max=10) for name in names]))
        limits.start()
        self.addCleanup(limits.stop)


    def test_no_profiles(self):
        with patch('ecs_data.profiles', return_value={}):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run()
        self.assertFalse(app.exception)
        self.assertIn('No hay perfiles', app.warning[0].value)

    def test_full_dashboard_and_empty_metrics(self):
        now = datetime.now(timezone.utc)
        inventory = pd.DataFrame([dict(service='api', desired=2, running=1, pending=1,
                                       status='ACTIVE', task_definition='demo:1')])
        session = Mock(region_name='us-east-1')
        client = Mock()
        client.get_caller_identity.return_value = {'Account': '123456789012'}
        frame = pd.DataFrame([dict(timestamp=now, service='api', metric='CPUUtilization', value=42)])
        with patch('ecs_data.profiles', return_value={'demo': '123456789012'}), \
             patch('ecs_data.session_for', return_value=session), \
             patch('ecs_data.client', return_value=client), \
             patch('ecs_data.clusters', return_value=['arn:aws:ecs:us-east-1:123456789012:cluster/demo']), \
             patch('ecs_data.services', return_value=inventory), \
             patch('ecs_data.history', return_value=(frame, [], now, now, 300)) as history:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[1].value, '2')
            self.assertEqual(app.metric[2].value, '1')
            table = app.dataframe[0].value
            self.assertNotIn('task_definition', table.columns)
            self.assertEqual(table.iloc[0].cpu_mean, 12.5)
            self.assertEqual(table.iloc[0].cpu_max, 80.0)
            self.assertEqual(table.iloc[0].tasks_min, 1)
            self.assertEqual(table.iloc[0].tasks_max, 10)
            self.assertTrue(any('Sin puntos' in info.value for info in app.info))
            app.sidebar.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertEqual(history.call_count, 2)

    def test_rds_navigation_history_and_refresh(self):
        now = datetime.now(timezone.utc)
        inventory = pd.DataFrame([dict(instance='db-a', engine='postgres', status='available')])
        frame = pd.DataFrame([
            dict(timestamp=now, instance='db-a', metric=metric, value=value, unit=unit)
            for metric, value, unit in [('CPUUtilization', 42, '%'),
                                       ('FreeableMemory', 1073741824, 'Bytes'),
                                       ('DatabaseConnections', 5, 'Count')]])
        client = Mock()
        client.get_caller_identity.return_value = {'Account': '123456789012'}
        with patch('ecs_data.profiles', return_value={'demo': '123456789012'}), \
             patch('ecs_data.session_for', return_value=Mock(region_name='us-east-1')), \
             patch('ecs_data.client', return_value=client), \
             patch('ecs_data.clusters', return_value=[]), \
             patch('rds_data.instances', return_value=inventory), \
             patch('rds_data.utilization_summary', return_value=(pd.DataFrame([
                 dict(instance='db-a', cpu_mean=20, cpu_max=42, memory_mean=1.5, memory_max=2)
             ]), [], now, now)) as utilization, \
             patch('rds_data.history', return_value=(frame, [], now, now, 300)) as history:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run()
            app.switch_page('app_pages/rds.py').run()
            self.assertFalse(app.exception)
            table = app.dataframe[0].value
            self.assertEqual(table.iloc[0].cpu_mean, 20)
            self.assertEqual(table.iloc[0].cpu_max, 42)
            self.assertEqual(table.iloc[0].memory_mean, 1.5)
            self.assertEqual(table.iloc[0].memory_max, 2)
            self.assertEqual(utilization.call_args.args[1], ['db-a'])
            self.assertEqual([item.value for item in app.subheader][:3],
                             ['CPU (%)', 'Memoria disponible (GiB)', 'Conexiones'])
            next(w for w in app.selectbox if w.label == 'Histórico').select(30).run()
            self.assertFalse(app.exception)
            self.assertEqual(history.call_args.args[2], 30)
            self.assertEqual(utilization.call_args.args[2], 30)
            calls = history.call_count
            app.sidebar.button[0].click().run()
            self.assertEqual(history.call_count, calls + 1)
            self.assertFalse(app.exception)

    def test_elasticache_navigation_history_and_refresh(self):
        now = datetime.now(timezone.utc)
        inventory = pd.DataFrame([dict(node='redis-a/0001', engine='postgres', status='available')])
        frame = pd.DataFrame([
            dict(timestamp=now, node='redis-a/0001', metric=metric, value=value, unit=unit)
            for metric, value, unit in [('CPUUtilization', 42, '%'),
                                       ('FreeableMemory', 1073741824, 'Bytes'),
                                       ('CurrConnections', 5, 'Count')]])
        client = Mock()
        client.get_caller_identity.return_value = {'Account': '123456789012'}
        with patch('ecs_data.profiles', return_value={'demo': '123456789012'}), \
             patch('ecs_data.session_for', return_value=Mock(region_name='us-east-1')), \
             patch('ecs_data.client', return_value=client), \
             patch('ecs_data.clusters', return_value=[]), \
             patch('elasticache_data.nodes', return_value=inventory), \
             patch('elasticache_data.utilization_summary', return_value=(pd.DataFrame([
                 dict(node='redis-a/0001', cpu_mean=20, cpu_max=42, memory_mean=1.5, memory_max=2, connections_mean=4, connections_max=8)
             ]), [], now, now)) as utilization, \
             patch('elasticache_data.history', return_value=(frame, [], now, now, 300)) as history:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run()
            app.switch_page('app_pages/elasticache.py').run()
            self.assertFalse(app.exception)
            table = app.dataframe[0].value
            self.assertEqual(table.iloc[0].cpu_mean, 20)
            self.assertEqual(table.iloc[0].cpu_max, 42)
            self.assertEqual(table.iloc[0].memory_mean, 1.5)
            self.assertEqual(table.iloc[0].memory_max, 2)
            self.assertEqual(utilization.call_args.args[1], ['redis-a/0001'])
            self.assertEqual(table.iloc[0].connections_mean, 4)
            self.assertEqual(table.iloc[0].connections_max, 8)
            self.assertEqual([item.value for item in app.subheader][:3],
                             ['CPU (%)', 'Memoria disponible (GiB)', 'Conexiones'])
            next(w for w in app.selectbox if w.label == 'Histórico').select(30).run()
            self.assertFalse(app.exception)
            self.assertEqual(history.call_args.args[2], 30)
            self.assertEqual(utilization.call_args.args[2], 30)
            calls = history.call_count
            app.sidebar.button[0].click().run()
            self.assertEqual(history.call_count, calls + 1)
            self.assertFalse(app.exception)

    def test_large_clusters_inventory_independent_of_chart_selection(self):
        now = datetime.now(timezone.utc)
        inventories = {
            cluster: pd.DataFrame([
                dict(service=f"{cluster}-api-{i:02}", desired=2, running=1, pending=1,
                     status='ACTIVE', task_definition='demo:1')
                for i in range(count)])
            for cluster, count in [('prod', 68), ('shadow', 34), ('test', 2)]
        }
        client = Mock()
        client.get_caller_identity.return_value = {'Account': '123456789012'}
        empty = pd.DataFrame(columns=['timestamp', 'service', 'metric', 'value'])
        with patch('ecs_data.profiles', return_value={'demo': '123456789012'}), \
             patch('ecs_data.session_for', return_value=Mock(region_name='us-east-1')), \
             patch('ecs_data.client', return_value=client), \
             patch('ecs_data.clusters', return_value=list(inventories)), \
             patch('ecs_data.services', side_effect=lambda session, cluster: inventories[cluster]), \
             patch('ecs_data.history', return_value=(empty, [], now, now, 300)) as history:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[0].value, '68')
            self.assertEqual(len(app.dataframe[0].value), 68)
            self.assertEqual(len(next(w for w in app.selectbox if w.label == 'Servicio para mostrar en las gráficas').options), 68)
            self.assertEqual(history.call_args.args[2], ['prod-api-00'])
            next(w for w in app.selectbox if w.label == 'Servicio para mostrar en las gráficas').select('prod-api-01').run()
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[0].value, '68')
            self.assertEqual(app.metric[1].value, '136')
            self.assertEqual(len(app.dataframe[0].value), 68)
            self.assertEqual(history.call_args.args[2], ['prod-api-01'])
            self.assertEqual(self.cpu_summary.call_count, 1)
            next(w for w in app.selectbox if w.label == 'Histórico').select(7).run()
            self.assertEqual(self.cpu_summary.call_args.args[3], 7)
            self.assertEqual(len(self.cpu_summary.call_args.args[2]), 68)
            for cluster, count in [('shadow', 34), ('test', 2)]:
                next(w for w in app.selectbox if w.label == 'Clúster').select(cluster).run()
                self.assertFalse(app.exception)
                self.assertEqual(app.metric[0].value, str(count))
                self.assertEqual(len(app.dataframe[0].value), count)
                self.assertEqual(len(next(w for w in app.selectbox if w.label == 'Servicio para mostrar en las gráficas').options), count)
                self.assertEqual(history.call_args.args[2], [f'{cluster}-api-00'])
                self.assertEqual(history.call_args.args[1], cluster)


if __name__ == '__main__':
    unittest.main()
