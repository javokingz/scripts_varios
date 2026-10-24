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


if __name__ == '__main__':
    unittest.main()
