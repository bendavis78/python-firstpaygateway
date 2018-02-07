import json
import logging
import re
import requests
from dateutil import tz
from dateutil.parser import parse as parse_date
from datetime import date, datetime
from sys import version_info
from . import errors

if (version_info > (3, 0)):
    # Import urljoin for Python 3
    from urllib.parse import urljoin
else:
    # Import urljoin for Python 2
    from urlparse import urljoin


logger = logging.getLogger(__name__)

ISO_DATETIME_RE = re.compile(r'^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?$')
DATETIME_RE = re.compile(r'^\d{2}\d{2}\d{4} \d{1,2}:\d{2}( ([AaPp]Mm))?$')
DATE_RE = re.compile(r'^\d{2}\d{2}\d{4}$')
TIME_RE = re.compile(r'\d{1:2}:\d{2}( ([AP]M))?$')
TZ_AWARE_FIELDS = ['trans_date_and_time']

def _extract_datetime(value):
    if not isinstance(value, str):
        return
    if ISO_DATETIME_RE.match(value):
        return parse_date(value)
    if DATE_RE.match(value):
        return parse_date(value).date()
    if TIME_RE.match(value):
        return parse_date(value).time()
    return None

def _to_camel_case(s):
    return re.sub(r'(?!^)_([A-z])', lambda m: m.group(1).upper(), s)


def _to_lower_underscore(s):
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', s)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def _json_default(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return str(obj)


def _json_dump(obj):
    return json.dumps(obj, indent=2, default=_json_default)


def _get_date_params(prefix, dt):
    """
    Converts date or datetime object to UTC params compatible with the API
    """
    if isinstance(dt, date):
        if prefix == 'end':
            dt = datetime.combine(dt, datetime.max.time())
        else:
            dt = datetime.combine(dt, datetime.min.time())

    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        # Make timezone-aware using local tz
        dt = dt.replace(tzinfo=tz.tzlocal())

    # convert to UTC
    utc_dt = dt.astimezone(tz.tzutc())

    return {
        'query_' + prefix + '_year': utc_dt.year,
        'query_' + prefix + '_month': utc_dt.month,
        'query_' + prefix + '_day': utc_dt.day,
        'query_' + prefix + '_hour': utc_dt.hour % 12,
        'query_' + prefix + '_minute': utc_dt.minute,
        'query_' + prefix + '_AMPM': utc_dt.hour < 12 and 'AM' or 'PM'
    }


class ResultObject(object):
    def __init__(self, data, opts):
        self._data = data
        self._opts = opts

    def _get_date_value(self, attrname, value):
        date_value = _extract_datetime(value)
        if date_value:
            gateway_tz = tz.gettz(self._opts['gateway_tz'])
            if attrname in TZ_AWARE_FIELDS and gateway_tz:
                # make timezone aware and convert to UTC
                date_value = date_value.replace(tzinfo=gateway_tz)
                date_value = date_value.astimezone(tzinfo=tz.tzutc())
        return date_value

    def __getattr__(self, attr):
        # convert key from lower_underscore to camelCase
        key = _to_camel_case(attr)

        if key not in self._data:
            # if camelCase doesn't work, try PascaleCase
            key = key[0].upper() + key[1:]

        if key not in self._data:
            raise AttributeError("'{}' object has no attribute {}*'".format(
                self.__class__.__name__, attr))


        value = self._data[key]

        date_value = self._get_date_value(key, value)
        if date_value:
            return date_value
        if type(value) == dict:
            return ResultObject(value, self._opts)
        if type(value) == list:
            return [type(o) == dict and ResultObject(o, self._opts) or o 
                    for o in value]
        return value

    def __setattr__(self, attr, value):
        if not attr.startswith('_'):
            import ipdb; ipdb.set_trace()
            raise AttributeError("Result attributes are read-only")
        super(ResultObject, self).__setattr__(attr, value)

    def __dir__(self):
        return [_to_lower_underscore(k) for k in self._data.keys()]

    def __repr__(self):
        def change_repr(o):
            new = {}
            for key, val in o.items():
                new_key = _to_lower_underscore(key)
                if type(val) == dict:
                    val = change_repr(val)
                elif type(val) == list:
                    val = [change_repr(o) for o in val]
                else:
                    date_val = self._get_date_value(new_key, val)
                    val = date_val and date_val.isoformat() or val
                new[new_key] = val
            return new
        return _json_dump(change_repr(self._data))


class Result(ResultObject):
    def __init__(self, response, opts):
        self._raw_response = response
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            data = {
                'isSuccess': False,
                'errorMessages': [response.text]
            }
        super(Result, self).__init__(data, opts)


class Client(object):
    VERSION = '1.2.0'

    def __init__(self, merchant_key, processor_id, gateway_tz=None, 
                 test_mode=False):
        self.merchant_key = merchant_key
        self.processor_id = processor_id
        self.test_mode = test_mode
        self.result_opts = {
            'gateway_tz': gateway_tz
        }

    @property
    def url(self):
        domain = 'secure.1stpaygateway.net'
        if self.test_mode:
            domain = 'secure-v.goemerchant.com'
        return 'https://{}/secure/RestGW/Gateway/Transaction/'.format(domain)

    def request(self, action, data):
        post_data = {}
        for key, value in data.items():
            # convert key from lower_underscore to camelCase
            key = re.sub(r'(?!^)_([A-z])', lambda m: m.group(1).upper(), key)
            post_data[key] = value

        post_data.update({
            'merchantKey': self.merchant_key,
            'processorId': self.processor_id
        })

        url = urljoin(self.url, action)
        headers = {'Content-Type': 'application/json', 'Charset': 'utf-8'}
        payload = json.dumps(post_data, default=_json_default, indent=2)

        logger.debug('Sending payment gateway request')
        logger.debug('  URL: ' + url)
        logger.debug('  HEADERS: ' + json.dumps(headers))
        logger.debug('  POST DATA: ' + payload)

        response = requests.post(url, headers=headers, data=payload)

        result = Result(response, self.result_opts)
        logger.debug('Gateway Response: ' + response.text)
        if not result.is_success:
            if result.validation_has_failed:
                raise errors.GatewayValidationError(result)
            raise errors.GatewayError(result)

        logger.debug('  RESULT: ' + _json_dump(result._data))
        return result

    def create_auth(self, **data):
        return self.request('Auth', data)

    def create_auth_using_vault(self, **data):
        return self.request('AuthUsingValut', data)

    def create_sale(self, **data):
        return self.request('Sale', data)

    def create_sale_vault(self, **data):
        return self.request('SaleUsingVault', data)

    def create_credit(self, **data):
        return self.request('Credit', data)

    def create_credit_retail_only(self, **data):
        return self.request('CreditRetailOnly', data)

    def create_credit_retail_only_using_vault(self, **data):
        return self.request('CreditRetailOnlyUsingVault', data)

    def perform_void(self, **data):
        return self.request('Void', data)

    def create_re_auth(self, **data):
        return self.request('ReAuth', data)

    def create_re_sale(self, **data):
        return self.request('ReSale', data)

    def create_re_debit(self, **data):
        return self.request('ReDebit', data)

    def query(self, **data):
        # Add some extra help with dates
        start_date = data.pop('start_date', None)
        end_date = data.pop('end_date', None)
        if start_date or end_date:
            data['query_time_zone_offset'] = 0
        if start_date:
            data.update(_get_date_params('start', start_date))
        if end_date:
            data.update(_get_date_params('end', end_date))
        return self.request('Query', data)

    def close_batch(self, **data):
        return self.request('CloseBatch', data)

    def perform_settle(self, **data):
        return self.request('Settle', data)

    def apply_tip_adjust(self, **data):
        return self.request('TipAdjust', data)

    def perform_ach_void(self, **data):
        return self.request('AchVoid', data)

    def create_ach_credit(self, **data):
        return self.request('AchCredit', data)

    def create_ach_debit(self, **data):
        return self.request('AchDebit', data)

    def create_ach_credit_using_vault(self, **data):
        return self.request('AchCreditUsingVault', data)

    def create_ach_debit_using_vault(self, **data):
        return self.request('AchDebitUsingVault', data)

    def get_ach_categories(self, **data):
        return self.request('AchGetCategories', data)

    def create_ach_categories(self, **data):
        return self.request('AchCreateCategory', data)

    def delete_ach_categories(self, **data):
        return self.request('AchDeleteCategory', data)

    def setup_ach_store(self, **data):
        return self.request('AchSetupStore', data)

    def create_vault_container(self, **data):
        return self.request('VaultCreateContainer', data)

    def create_vault_ach_record(self, **data):
        return self.request('VaultCreateAchRecord', data)

    def create_vault_credit_card_record(self, **data):
        return self.request('VaultCreateCCRecord', data)

    def create_vault_shipping_record(self, **data):
        return self.request('VaultCreateShippingRecord', data)

    def delete_vault_container_and_all_assc_data(self, **data):
        return self.request('VaultDeleteContainerAndAllAsscData', data)

    def delete_vault_ach_record(self, **data):
        return self.request('VaultDeleteAchRecord', data)

    def delete_vault_credit_card_record(self, **data):
        return self.request('VaultDeleteCCRecord', data)

    def delete_vault_shipping_record(self, **data):
        return self.request('VaultDeleteShippingRecord', data)

    def update_vault_container(self, **data):
        return self.request('VaultUpdateContainer', data)

    def update_vault_ach_record(self, **data):
        return self.request('VaultUpdateAchRecord', data)

    def update_vault_credit_card_record(self, **data):
        return self.request('VaultUpdateCCRecord', data)

    def update_vault_shipping_record(self, **data):
        return self.request('VaultUpdateShippingRecord', data)

    def query_vault(self, **data):
        return self.request('VaultQueryVault', data)

    def query_vault_for_credit_card_records(self, **data):
        return self.request('VaultQueryCCRecord', data)

    def query_vault_for_ach_records(self, **data):
        return self.request('VaultQueryAchRecord', data)

    def query_vault_for_shipping_records(self, **data):
        return self.request('VaultQueryShippingRecord', data)

    def modify_recurring(self, **data):
        return self.request('RecurringModify', data)

    def submit_acct_updater(self, **data):
        return self.request('AccountUpdaterSubmit', data)

    def submit_acct_updater_vault(self, **data):
        return self.request('AccountUpdaterSubmitVault', data)

    def get_acct_updater_return(self, **data):
        return self.request('AccountUpdaterReturn', data)
