import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import requests

import db


class DataGrabRetryTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.original_cwd = os.getcwd()
        os.chdir(cls.temp_dir.name)
        os.environ['BOT_TOKEN'] = 'test-token'
        os.environ['OWNER_TG_ID'] = '1'
        os.environ['DATAGRAB_KEY'] = 'test-key'
        sys.modules.pop('main', None)
        cls.main = importlib.import_module('main')

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.original_cwd)
        cls.temp_dir.cleanup()

    async def test_retries_connect_timeout_and_then_succeeds(self):
        pdf_path = Path(self.temp_dir.name) / 'receipt.pdf'
        pdf_path.write_bytes(b'%PDF-1.4 test')
        response = Mock(status_code=200, text='')
        response.json.return_value = {'result': 'sber', 'is_fake': False}

        with (
            patch.object(
                self.main.requests,
                'post',
                side_effect=[
                    requests.exceptions.ConnectTimeout('first timeout'),
                    requests.exceptions.ConnectTimeout('second timeout'),
                    response,
                ],
            ) as request_mock,
            patch.object(self.main.asyncio, 'sleep', new_callable=AsyncMock) as sleep_mock,
        ):
            data, errors = await self.main.upload_receipt_to_datagrab(
                file_path=str(pdf_path),
                file_name='receipt.pdf',
                sender_id=123,
            )

        self.assertEqual(data['result'], 'sber')
        self.assertEqual(errors, [])
        self.assertEqual(request_mock.call_count, 3)
        sleep_mock.assert_has_awaits([unittest.mock.call(1), unittest.mock.call(2)])

    async def test_stops_after_three_connect_timeouts(self):
        pdf_path = Path(self.temp_dir.name) / 'timeout.pdf'
        pdf_path.write_bytes(b'%PDF-1.4 test')

        with (
            patch.object(
                self.main.requests,
                'post',
                side_effect=requests.exceptions.ConnectTimeout('timeout'),
            ) as request_mock,
            patch.object(self.main.asyncio, 'sleep', new_callable=AsyncMock),
        ):
            data, errors = await self.main.upload_receipt_to_datagrab(
                file_path=str(pdf_path),
                file_name='timeout.pdf',
                sender_id=123,
            )

        self.assertIsNone(data)
        self.assertEqual(request_mock.call_count, 3)
        self.assertEqual(
            errors,
            ['api.datagrab.ru: таймаут подключения после 3 попыток'],
        )

    async def test_does_not_retry_http_error(self):
        pdf_path = Path(self.temp_dir.name) / 'server-error.pdf'
        pdf_path.write_bytes(b'%PDF-1.4 test')
        response = Mock(status_code=500, text='internal error')

        with patch.object(self.main.requests, 'post', return_value=response) as request_mock:
            data, errors = await self.main.upload_receipt_to_datagrab(
                file_path=str(pdf_path),
                file_name='server-error.pdf',
                sender_id=123,
            )

        self.assertIsNone(data)
        self.assertEqual(errors, ['api.datagrab.ru: HTTP 500'])
        self.assertEqual(request_mock.call_count, 1)

    def test_broken_fallback_is_not_configured(self):
        self.assertEqual(
            self.main.DATAGRAB_UPLOAD_URL,
            'https://api.datagrab.ru/upload.php',
        )
        self.assertNotIn('api2.datagrab.ru', self.main.DATAGRAB_UPLOAD_URL)

    def test_recovered_user_is_in_default_allowlist(self):
        self.assertIn(8263217831, self.main.DEFAULT_ALLOWED_TG_IDS)

    def test_railway_volume_path_is_used_for_database(self):
        self.assertEqual(
            db.resolve_db_path('/data'),
            '/data/bot_data.db',
        )


if __name__ == '__main__':
    unittest.main()
