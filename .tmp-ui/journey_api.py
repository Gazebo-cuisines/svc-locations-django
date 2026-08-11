import json
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.test import Client, RequestFactory
from locations.models import Location, LocationAddress, LocationContact
from users_rbac.models import Department, RbacUser, UserDepartment

# Ensure an IT user exists for request simulation
user, _ = RbacUser.objects.get_or_create(
    cognito_sub='ui-journey-it',
    defaults={
        'username': 'ui_journey_it',
        'email': 'ui_journey_it@example.com',
        'display_name': 'UI Journey IT',
        'is_active': True,
    },
)
UserDepartment.objects.get_or_create(user=user, department=Department.IT)

# Patch attach_user to inject this user when Authorization present
import users_rbac.auth as auth_mod
import locations.views.location_views as loc_views

_orig_attach = auth_mod.attach_user

def fake_attach(request, *, missing='error', invalid='error'):
    header = request.headers.get('Authorization', '')
    if header.startswith('Bearer journey-it'):
        request.rbac_user = user
        request.client_ip = '127.0.0.1'
        return None
    return _orig_attach(request, missing=missing, invalid=invalid)

auth_mod.attach_user = fake_attach
# location_views imported attach_user by name — patch there too
loc_views.attach_user = fake_attach
from locations.views import contact_views, address_views
contact_views._require_admin  # uses loc_views._require_admin
# _require_admin closes over loc_views.attach_user — already patched

c = Client()
AUTH = {'HTTP_AUTHORIZATION': 'Bearer journey-it', 'HTTP_X_API_TOKEN': 'dev-static-token'}

SUPPLIERS = [
    {
        'code': '366001',
        'name': 'Bid Food    (Bidvest Food)',
        'contact': 'Thomas - 07858374804',
        'phone': '0370 3663 250/mobile 078011488',
        'email': 'sloughsalescentre@bidfood.co.uk',
        'lines': ['814 LEIGH ROAD', 'SLOUGH TRADING ESTATE', 'SLOUGH', 'BUCKS', 'SL1 4BD'],
    },
    {
        'code': '3MH001',
        'name': 'GEM SCIENTIFIC (3M HEALTH CARE LIMITED)',
        'contact': 'Steve/Katie',
        'phone': '01509 613191',
        'email': 'SALES@GEMSCIENTIFIC.CO.UK; gwenl@gemscientific.co.uk',
        'lines': ['Unit 301 Baley Enterprise Centre', '513 Bradford Road', 'Batley', 'West Yorkshire', 'WF17 8LL'],
    },
    {
        'code': 'ABB001',
        'name': 'ABBEY REFRIGERATED TRANSPORT LTD',
        'contact': 'Elaine - Accounts',
        'phone': '01582 873765',
        'email': 'ra@hsbc.com',
        'lines': ['P.O. Box 4001', 'Dunstable', 'Beds', 'LU6 2ZZ', ''],
    },
]

print('=== PRECHECK codes ===')
for s in SUPPLIERS:
    exists = list(Location.objects.filter(external_code=s['code']).values_list('id', 'name'))
    print(s['code'], exists or 'NOT FOUND')

results = []

def post(path, body):
    return c.post(path, data=json.dumps(body), content_type='application/json', **AUTH)

def get(path):
    return c.get(path, **AUTH)

print('\n=== SAD: create without auth ===')
r = c.post('/container/locations/', data=json.dumps({'name': 'X', 'roles': ['supplier']}), content_type='application/json')
print('no auth', r.status_code, r.content[:120])

print('\n=== SAD: duplicate / empty name ===')
r = post('/container/locations/', {'roles': ['supplier']})
print('missing name', r.status_code, r.json().get('message'))

print('\n=== HAPPY: create 3 suppliers + contact + address ===')
created_ids = []
for s in SUPPLIERS:
    # clean prior journey rows if re-run
    for loc in Location.objects.filter(external_code=s['code'], name=s['name']):
        loc.delete()

    r = post('/container/locations/', {
        'name': s['name'],
        'external_code': s['code'],
        'roles': ['supplier'],
        'visible': True,
    })
    print('CREATE', s['code'], r.status_code, r.json().get('message'))
    if r.status_code != 201:
        print('  body', r.content[:300])
        results.append(('FAIL create', s['code'], r.status_code))
        continue
    loc_id = r.json()['data']['id']
    created_ids.append(loc_id)

    r = post(f'/container/locations/{loc_id}/contacts/', {
        'name': s['contact'],
        'phone': s['phone'],
        'email': s['email'],
    })
    print(' CONTACT', s['code'], r.status_code, r.json().get('message'))
    if r.status_code != 201:
        results.append(('FAIL contact', s['code'], r.status_code))

    payload = {
        'name': 'Depot',
        'is_primary': True,
        'address_line_1': s['lines'][0] or None,
        'address_line_2': s['lines'][1] or None,
        'address_line_3': s['lines'][2] or None,
        'address_line_4': s['lines'][3] or None,
        'address_line_5': s['lines'][4] or None,
    }
    r = post(f'/container/locations/{loc_id}/postal-address/', payload)
    print(' ADDRESS', s['code'], r.status_code, r.json().get('message'))
    if r.status_code != 201:
        results.append(('FAIL address', s['code'], r.status_code))
        continue
    addr = r.json()['data']['address']
    print('  joined=', repr(addr))

    # verify GET supplier detail
    r = get(f'/container/suppliers/{loc_id}/')
    data = r.json()['data']
    print(' DETAIL', s['code'], r.status_code,
          'contacts', len(data.get('contacts') or []),
          'addresses', len(data.get('addresses') or []))

print('\n=== SAD: duplicate external_code ===')
r = post('/container/locations/', {
    'name': 'Dup Bid Food',
    'external_code': '366001',
    'roles': ['supplier'],
})
print('dup', r.status_code, r.json().get('message'))

print('\n=== SAD: wrong method on suppliers ===')
r = c.patch('/container/suppliers/%s/' % created_ids[0], data='{}', content_type='application/json', **AUTH)
print('patch suppliers', r.status_code)

print('\n=== phone length check ===')
# ABB phone fine; Bid Food phone length
phone = '0370 3663 250/mobile 078011488'
print('phone len', len(phone), 'field max', LocationContact._meta.get_field('phone').max_length)

print('\nCREATED IDS', created_ids)
print('ISSUES', results)
