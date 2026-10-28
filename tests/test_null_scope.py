import pytest
from maihush.host.config import MaiHushConfig
from maihush.host.messages import route_for
from maihush.domain.models import Denied

def test_null_scope_is_explicit_managed_route_not_wildcard():
    config=MaiHushConfig.model_validate({'routes':[{'enabled':True,'route_scope':''}]})
    message={'platform':'qq','account_id':'123456789','scope':None,'session_id':'synthetic-test-stream',
             'message_info':{'group_info':{'group_id':'987654321'}}}
    assert route_for(config,message).route_scope==''
    for scope in ('named','',123):
        with pytest.raises(Denied): route_for(config,{**message,'scope':scope})
