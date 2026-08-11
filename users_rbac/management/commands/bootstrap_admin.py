"""Bootstrap first Cognito + RbacUser with admin grants (one-time prod seed)."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from users_rbac.grants import apply_grants
from users_rbac.models import RbacUser
from users_rbac.services import create_identity


class Command(BaseCommand):
    help = 'Create first admin identity in Cognito + Django with full admin grants.'

    def add_arguments(self, parser):
        parser.add_argument('--username', required=True)
        parser.add_argument('--password', required=True)
        parser.add_argument('--email', default='')
        parser.add_argument('--display-name', default='Admin')

    def handle(self, *args, **options):
        username = options['username'].strip()
        password = options['password']
        email = (options['email'] or '').strip() or None
        display_name = (options['display_name'] or 'Admin').strip()

        if RbacUser.objects.filter(username=username).exists():
            user = RbacUser.objects.get(username=username)
            self.stdout.write(f'User {username} already exists (id={user.id}); refreshing admin grants.')
        else:
            try:
                user = create_identity(
                    username,
                    password,
                    email=email,
                    display_name=display_name,
                    created_by_sub='bootstrap',
                )
            except ValueError as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(self.style.SUCCESS(f'Created {username} (id={user.id}, sub={user.cognito_sub})'))

        with transaction.atomic():
            apply_grants(
                user,
                {
                    'departments': ['admin'],
                    'production_areas': [],
                    'warehouse': [],
                    'admin_areas': ['technical', 'operational', 'npd', 'finance'],
                },
            )
        self.stdout.write(self.style.SUCCESS('Admin grants applied. Login via POST /auth/login/'))
