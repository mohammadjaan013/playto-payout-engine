"""
Custom test runner that terminates all other PostgreSQL backend connections
before Django drops the test database.

Problem: Celery workers (or other long-running processes) hold open connections
to the test_* database. PostgreSQL refuses to DROP a database that has active
sessions, so Django's teardown blows up with "database is being accessed by
other users" – even though every test already passed.

Fix: run pg_terminate_backend() against all sessions on the test database
immediately before issuing DROP DATABASE.
"""

from django.test.runner import DiscoverRunner
from django.db.backends.postgresql.creation import DatabaseCreation as PgCreation


class TerminatingDatabaseCreation(PgCreation):
    """PostgreSQL DatabaseCreation that force-kills stray sessions before DROP."""

    def _destroy_test_db(self, test_database_name, verbosity):
        # Terminate every connection to the test DB except our own.
        with self._nodb_cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) "
                "FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                [test_database_name],
            )
        super()._destroy_test_db(test_database_name, verbosity)


class CleanTestRunner(DiscoverRunner):
    """DiscoverRunner that patches the DB creation class for every connection."""

    def setup_databases(self, **kwargs):
        from django.db import connections

        for alias in connections:
            conn = connections[alias]
            if conn.vendor == "postgresql":
                conn.creation = TerminatingDatabaseCreation(conn)

        return super().setup_databases(**kwargs)
