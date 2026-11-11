from concurrent.futures import ThreadPoolExecutor
from app_v2.auth import Accounts


def test_parallel_sessions_do_not_disappear_during_polling(tmp_path):
    accounts=Accounts(tmp_path/'accounts.sqlite')
    accounts.create('operator','Operator','analyst','parallel-test-password')
    stable,_=accounts.login('operator','parallel-test-password')
    def poll(_):
        for i in range(200):
            assert accounts.session(stable)=={'id':'operator','name':'Operator','role':'analyst'}
            assert accounts.users()[0]['id']=='operator'
    def renew(_):
        for i in range(12):
            other,_=accounts.login('operator','parallel-test-password')
            assert accounts.session(other)['role']=='analyst'
            accounts.logout(other)
            assert accounts.session(other) is None
    try:
        with ThreadPoolExecutor(max_workers=9) as pool:
            results=[pool.submit(poll,i) for i in range(8)]+[pool.submit(renew,0)]
            for result in results:result.result()
        assert accounts.session(stable)['id']=='operator'
    finally:accounts.close()
