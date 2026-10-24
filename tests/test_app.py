import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
from streamlit.testing.v1 import AppTest


class DashboardTests(unittest.TestCase):
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
            self.assertTrue(any('Sin puntos' in info.value for info in app.info))
            app.sidebar.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertEqual(history.call_count, 2)

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
            self.assertEqual(len(app.multiselect[0].value), 68)
            self.assertEqual(len(history.call_args.args[2]), 68)
            app.multiselect[0].set_value(['prod-api-00']).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[0].value, '68')
            self.assertEqual(app.metric[1].value, '136')
            self.assertEqual(len(app.dataframe[0].value), 68)
            self.assertEqual(history.call_args.args[2], ['prod-api-00'])
            app.multiselect[0].set_value([]).run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.dataframe[0].value), 68)
            for cluster, count in [('shadow', 34), ('test', 2)]:
                next(w for w in app.selectbox if w.label == 'Clúster').select(cluster).run()
                self.assertFalse(app.exception)
                self.assertEqual(app.metric[0].value, str(count))
                self.assertEqual(len(app.dataframe[0].value), count)
                self.assertEqual(len(app.multiselect[0].value), count)
                self.assertEqual(history.call_args.args[1], cluster)


if __name__ == '__main__':
    unittest.main()
