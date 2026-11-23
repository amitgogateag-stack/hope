"""Proposal guards; native permission and rollback behavior is separate."""
from copy import deepcopy
from dataclasses import FrozenInstanceError,replace
import json
import pytest
from hope.api.observer_acl_plan import propose_temp_restriction

def boundary():
    def role(oid,name):return dict(oid=oid,name=name,login=True,superuser=False,createdb=False,createrole=False,replication=False,bypassrls=False,inherit=True,connect=True,create=False,temp=True,member_of=[oid],inherited=[oid])
    return {'schema':'hope.observer-temp-boundary.v1','database':{'oid':4,'name':'lab','owner':1},'cluster':['123',16,False],'observer':'reader','role_count':3,'roles':[role(1,'owner'),role(2,'reader'),role(3,'app')],'memberships':[],'acl':[[1,0,'CONNECT',False],[1,0,'TEMPORARY',False],[1,1,'CONNECT',False],[1,1,'CREATE',False],[1,1,'TEMPORARY',False]]}


def test_proposal_is_immutable_diagnostic_and_only_changes_temp_effective_rights():
    value=boundary();p=propose_temp_restriction(value,application_roles=('owner','app'))
    assert p.preserved_roles==('app',) and p.affected_roles==('reader',)
    with pytest.raises(FrozenInstanceError):p.before_json='untrusted'
    after=json.loads(p.after_json)
    assert [r['temp'] for r in after['roles']]==[True,False,True]
    assert value==boundary() and not hasattr(p,'execution_authorized')
    value['roles'][0]['name']='mutated';assert json.loads(p.before_json)==boundary()


@pytest.mark.parametrize('fault',['schema','inventory','cluster','recovery','owner','observer','public','semantics','identifier','unknown_app','overlap'])
def test_unreviewed_or_unsupported_inputs_are_refused(fault):
    b=boundary();apps=('owner','app')
    if fault=='schema':b['schema']='untrusted'
    elif fault=='inventory':b['role_count']+=1
    elif fault=='cluster':b['cluster'][1]=18
    elif fault=='recovery':b['cluster'][2]=True
    elif fault=='owner':b['database']['owner']=999
    elif fault=='observer':b['roles'][1]['superuser']=True
    elif fault=='public':b['acl'][1][3]=True
    elif fault=='semantics':b['roles'][1]['temp']=False
    elif fault=='identifier':b['database']['name']='lab;DROP DATABASE lab'
    elif fault=='unknown_app':apps=('not_a_role',)
    else:b['roles'][1]['member_of'].append(3)
    with pytest.raises(ValueError):propose_temp_restriction(b,application_roles=apps)


@pytest.mark.parametrize('delimiter',['$expected$','$proposal$'])
def test_catalog_name_cannot_escape_generated_sql_dollar_quoting(delimiter):
    b=boundary();b['roles'].append(dict(b['roles'][0],oid=9,name=delimiter,login=False,member_of=[9],inherited=[9]));b['role_count']+=1
    p=propose_temp_restriction(b,application_roles=('owner','app'))
    with pytest.raises(ValueError,match='SQL_LITERAL_INVALID'):p.sql()


def test_string_oid_representation_is_not_silently_accepted_as_numeric_identity():
    b=boundary();b['acl'][1][1]='0'
    with pytest.raises(ValueError,match='OID_INVALID'):
        propose_temp_restriction(b,application_roles=('owner','app'))
