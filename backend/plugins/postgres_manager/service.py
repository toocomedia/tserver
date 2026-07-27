"""PostgreSQL Manager remote-access business logic."""
import logging
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class PostgresService:
    # Remote Access & SSL configuration belongs to the manager plugin.

    async def list_remote_domains(self, db: AsyncSession | None = None) -> list[dict[str, Any]]:
        """Return list of configured remote access domains from SQLite DB."""
        if db is None:
            return []
        try:
            from sqlalchemy import select
            from models.postgres_remote import PostgresRemoteDomain
            res = await db.scalars(select(PostgresRemoteDomain).order_by(PostgresRemoteDomain.created_at.desc()))
            records = list(res.all())
            return [
                {
                    "domain": r.full_domain,
                    "mode": r.mode,
                    "ssl_active": r.ssl_active,
                    "nginx_stream": r.nginx_stream,
                }
                for r in records
            ]
        except Exception as exc:
            logger.warning("Failed to list remote domains from DB: %s", exc)
            return []

    async def add_remote_domain(
        self,
        db: AsyncSession,
        mode: str,
        domain: str | None,
        subdomain: str | None,
        hostname: str | None,
        issue_ssl: bool = True,
    ) -> dict[str, Any]:
        """Add a new remote domain endpoint to SQLite DB, issue SSL, and write Nginx stream proxy."""
        from sqlalchemy import select
        from models.postgres_remote import PostgresRemoteDomain
        from models.domain import Domain

        domain_id = None
        if mode == "managed":
            if not domain or not subdomain:
                raise ValueError("Parent domain and subdomain prefix are required.")
            full_host = f"{subdomain.strip().rstrip('.')}.{domain.strip().lstrip('.')}"
            parent = await db.scalar(select(Domain).where(Domain.name == domain.strip()))
            if parent:
                domain_id = parent.id
                # Auto-create DNS A record for subdomain in PowerDNS
                try:
                    from services import dns_service
                    import config
                    await dns_service.add_a_record(parent.name, subdomain.strip(), getattr(config, "SERVER_IP", "127.0.0.1"), 300)
                except Exception as exc:
                    logger.warning("Auto DNS A record creation for %s.%s failed: %s", subdomain, parent.name, exc)
        else:
            if not hostname:
                raise ValueError("External hostname is required.")
            full_host = hostname.strip()

        # Validate domain format
        import re
        if not re.match(r"^[a-zA-Z0-9.\-]{3,253}$", full_host):
            raise ValueError(f"Invalid hostname format: {full_host}")

        existing = await db.scalar(select(PostgresRemoteDomain).where(PostgresRemoteDomain.full_domain == full_host))
        if existing:
            raise ValueError(f"Domain '{full_host}' is already configured for remote access.")

        ssl_active = False
        if issue_ssl and os.name != "nt":
            ssl_active = self._issue_certbot_ssl(full_host)

        stream_written = self._write_nginx_stream_conf(full_host)

        record = PostgresRemoteDomain(
            domain_id=domain_id,
            mode=mode,
            subdomain=subdomain or "",
            full_domain=full_host,
            ssl_active=ssl_active,
            nginx_stream=stream_written or os.name == "nt",
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)

        return {
            "domain": record.full_domain,
            "mode": record.mode,
            "ssl_active": record.ssl_active,
            "nginx_stream": record.nginx_stream,
        }

    async def reissue_remote_ssl(self, db: AsyncSession, full_host: str) -> dict[str, Any]:
        """Re-issue Let's Encrypt SSL certificate for a specific domain endpoint in SQLite DB."""
        from sqlalchemy import select
        from models.postgres_remote import PostgresRemoteDomain

        target = await db.scalar(select(PostgresRemoteDomain).where(PostgresRemoteDomain.full_domain == full_host))
        if not target:
            raise ValueError(f"Domain '{full_host}' not found in remote access list.")

        ssl_active = False
        if os.name != "nt":
            ssl_active = self._issue_certbot_ssl(full_host)
            self._write_nginx_stream_conf(full_host)
        else:
            ssl_active = True

        target.ssl_active = ssl_active
        target.nginx_stream = True
        await db.commit()
        await db.refresh(target)

        return {
            "domain": target.full_domain,
            "mode": target.mode,
            "ssl_active": target.ssl_active,
            "nginx_stream": target.nginx_stream,
        }

    async def delete_remote_domain(self, db: AsyncSession, full_host: str) -> bool:
        """Remove a remote access domain from SQLite DB, delete PowerDNS A record, and remove Nginx stream config."""
        from sqlalchemy import select
        from models.postgres_remote import PostgresRemoteDomain

        target = await db.scalar(select(PostgresRemoteDomain).where(PostgresRemoteDomain.full_domain == full_host))
        if not target:
            raise ValueError(f"Domain '{full_host}' not found.")

        if target.mode == "managed" and target.subdomain and target.domain_id:
            try:
                from services import dns_service
                from models.domain import Domain
                parent = await db.scalar(select(Domain).where(Domain.id == target.domain_id))
                if parent:
                    await dns_service.delete_record(parent.name, target.subdomain, "A")
            except Exception as exc:
                logger.warning("DNS A record deletion failed for %s: %s", full_host, exc)

        await db.delete(target)
        await db.commit()
        return True


    def _issue_certbot_ssl(self, full_host: str) -> bool:
        """Deprecated compatibility shim; use native_tls.issue_shared_certificate."""
        logger.warning("Ignoring deprecated PostgreSQL certificate request for %s", full_host)
        return False



    def _write_nginx_stream_conf(self, full_host: str) -> bool:
        """Deprecated: PostgreSQL now owns TLS and listens directly."""
        logger.warning("Ignoring deprecated Nginx PostgreSQL stream request for %s", full_host)
        return False


    def enable_remote(
        self,
        mode: str,
        domain: str | None,
        subdomain: str | None,
        hostname: str | None,
        issue_ssl: bool = True,
    ) -> dict[str, Any]:
        """Legacy enable_remote call wrapper."""
        return self.add_remote_domain(mode, domain, subdomain, hostname, issue_ssl)

    def disable_remote(self) -> dict[str, Any]:
        """Legacy disable_remote call wrapper."""
        domains = self.list_remote_domains()
        for d in domains:
            if d.get("domain"):
                self.delete_remote_domain(d["domain"])
        return {"enabled": False, "domain": None, "ssl_active": False, "nginx_stream": False}

    # ------------------------------------------------------------------
    # Native TLS overrides. These definitions intentionally come after the
    # legacy methods above so old imports remain compatible while all current
    # routes use the PostgreSQL-owned TLS implementation.
    # ------------------------------------------------------------------

    async def list_remote_domains(self, db: AsyncSession | None = None) -> list[dict[str, Any]]:
        if db is None:
            return []
        from plugins.postgres_manager.native_tls import list_states
        return await list_states(db)

    async def add_remote_domain(
        self, db: AsyncSession, mode: str, domain: str | None,
        subdomain: str | None, hostname: str | None, issue_ssl: bool = True,
        allowed_cidrs: list[str] | None = None,
    ) -> dict[str, Any]:
        from sqlalchemy import select
        from models.domain import Domain
        from models.postgres_remote import PostgresRemoteDomain
        from plugins.postgres_manager import native_tls

        full_host, _ = native_tls.build_hostname(mode, domain, subdomain, hostname)
        cidrs = native_tls.normalize_cidrs(allowed_cidrs or [])
        domain_id = None
        if mode == "managed":
            parent = await db.scalar(select(Domain).where(Domain.name == (domain or "").strip().lower()))
            if not parent:
                raise ValueError(f"Managed parent domain is not registered: {domain}")
            domain_id = parent.id
            from services import dns_service
            import config
            await dns_service.add_a_record(parent.name, (subdomain or "").strip().lower(), config.SERVER_IP, 300)
        await native_tls.resolve_host(full_host)

        existing = await db.scalar(select(PostgresRemoteDomain).where(PostgresRemoteDomain.full_domain == full_host))
        if existing and existing.enabled:
            raise ValueError(f"Domain '{full_host}' is already configured for remote access.")
        if existing:
            record = existing
            record.domain_id = domain_id
            record.mode = mode
            record.subdomain = (subdomain or "").strip().lower()
            record.allowed_cidrs = ",".join(cidrs)
            record.dns_status = "ready"
            record.tls_status = "pending" if issue_ssl else "disabled"
            record.postgres_status = "pending"
            record.enabled = False
            record.ssl_active = False
            record.last_error = None
        else:
            record = PostgresRemoteDomain(
                domain_id=domain_id, mode=mode, subdomain=(subdomain or "").strip().lower(),
                full_domain=full_host, allowed_cidrs=",".join(cidrs), dns_status="ready",
                tls_status="pending" if issue_ssl else "disabled", postgres_status="pending",
                enabled=False, ssl_active=False, nginx_stream=False,
            )
            db.add(record)
        await db.commit()

        try:
            rows = list((await db.scalars(select(PostgresRemoteDomain))).all())
            active_rows = [row for row in rows if row.enabled and row.id != record.id]
            hosts = [row.full_domain for row in active_rows] + [record.full_domain]
            all_cidrs = sorted({cidr for row in active_rows + [record] for cidr in row.allowed_cidrs.split(",") if cidr})
            if not issue_ssl:
                raise ValueError("TLS must be enabled before a public PostgreSQL endpoint can be activated.")
            cert_name, expiry = await native_tls.issue_shared_certificate(hosts)
            await native_tls.configure_postgres(hosts, all_cidrs)
            await native_tls.firewall_allow(all_cidrs)
            for row in active_rows + [record]:
                row.certificate_name = cert_name
                row.certificate_expiry = expiry
                row.tls_status = "ready"
                row.postgres_status = "ready"
                row.ssl_active = True
                row.enabled = True
                row.last_error = None
            await db.commit()
        except Exception as exc:
            record.last_error = str(exc)
            record.tls_status = "error"
            record.postgres_status = "error"
            record.enabled = False
            record.ssl_active = False
            await db.commit()
            raise ValueError(str(exc)) from exc
        await db.refresh(record)
        return await native_tls.endpoint_state(db, record)

    async def reissue_remote_ssl(self, db: AsyncSession, full_host: str) -> dict[str, Any]:
        from sqlalchemy import select
        from models.postgres_remote import PostgresRemoteDomain
        from plugins.postgres_manager import native_tls

        target = await db.scalar(select(PostgresRemoteDomain).where(PostgresRemoteDomain.full_domain == full_host))
        if not target:
            raise ValueError(f"Domain '{full_host}' not found in remote access list.")
        rows = list((await db.scalars(select(PostgresRemoteDomain))).all())
        active_rows = [row for row in rows if row.enabled or row.id == target.id]
        hosts = [row.full_domain for row in active_rows]
        cidrs = sorted({cidr for row in active_rows for cidr in row.allowed_cidrs.split(",") if cidr})
        try:
            for host in hosts:
                await native_tls.resolve_host(host)
            cert_name, expiry = await native_tls.issue_shared_certificate(hosts)
            await native_tls.configure_postgres(hosts, cidrs)
            await native_tls.firewall_allow(cidrs)
            for row in active_rows:
                row.certificate_name = cert_name
                row.certificate_expiry = expiry
                row.tls_status = "ready"
                row.postgres_status = "ready"
                row.ssl_active = True
                row.enabled = True
                row.last_error = None
            await db.commit()
        except Exception as exc:
            target.last_error = str(exc)
            target.tls_status = "error"
            target.postgres_status = "error"
            target.enabled = False
            target.ssl_active = False
            await db.commit()
            raise ValueError(str(exc)) from exc
        await db.refresh(target)
        return await native_tls.endpoint_state(db, target)

    async def test_remote_domain(self, db: AsyncSession, full_host: str) -> dict[str, Any]:
        from sqlalchemy import select
        from models.postgres_remote import PostgresRemoteDomain
        from plugins.postgres_manager import native_tls
        target = await db.scalar(select(PostgresRemoteDomain).where(PostgresRemoteDomain.full_domain == full_host))
        if not target:
            raise ValueError(f"Domain '{full_host}' not found in remote access list.")
        await native_tls.resolve_host(full_host)
        if not target.enabled or target.tls_status != "ready":
            raise ValueError("Endpoint is not active with native PostgreSQL TLS.")
        return await native_tls.endpoint_state(db, target)

    async def delete_remote_domain(self, db: AsyncSession, full_host: str) -> bool:
        from sqlalchemy import select
        from models.postgres_remote import PostgresRemoteDomain
        from plugins.postgres_manager import native_tls
        target = await db.scalar(select(PostgresRemoteDomain).where(PostgresRemoteDomain.full_domain == full_host))
        if not target:
            raise ValueError(f"Domain '{full_host}' not found.")
        cidrs = {value for value in target.allowed_cidrs.split(",") if value}
        if target.mode == "managed" and target.subdomain and target.domain_id:
            try:
                from services import dns_service
                from models.domain import Domain
                parent = await db.scalar(select(Domain).where(Domain.id == target.domain_id))
                if parent:
                    await dns_service.delete_record(parent.name, target.subdomain, "A")
            except Exception as exc:
                logger.warning("DNS record deletion failed for %s: %s", full_host, exc)
        await db.delete(target)
        await db.commit()
        remaining = list((await db.scalars(select(PostgresRemoteDomain))).all())
        retained = {cidr for row in remaining for cidr in row.allowed_cidrs.split(",") if cidr}
        await native_tls.firewall_remove(sorted(cidrs - retained))
        if remaining:
            active = [row for row in remaining if row.enabled]
            if active:
                hosts = [row.full_domain for row in active]
                active_cidrs = sorted({value for row in active for value in row.allowed_cidrs.split(",") if value})
                await native_tls.issue_shared_certificate(hosts)
                await native_tls.configure_postgres(hosts, active_cidrs)
            else:
                await native_tls.disable_remote_postgres()
        else:
            await native_tls.disable_remote_postgres()
        return True



# Module-level singleton — imported by router.py and other plugins
    async def list_remote_domains(self, db: AsyncSession | None = None) -> list[dict[str, Any]]:
        if db is None:
            return []
        from sqlalchemy import select
        from models.postgres_remote import PostgresRemoteDomain
        from plugins.postgres_manager.native_tls import endpoint_state
        rows = list((await db.scalars(select(PostgresRemoteDomain).order_by(PostgresRemoteDomain.created_at.desc()))).all())
        return [endpoint_state(row) for row in rows]

    async def add_remote_domain(self, db: AsyncSession, mode: str, domain: str | None,
                                subdomain: str | None, hostname: str | None,
                                issue_ssl: bool | None = None, allowed_cidrs: list[str] | None = None,
                                encryption_enabled: bool | None = None) -> dict[str, Any]:
        from sqlalchemy import select
        from models.domain import Domain
        from models.postgres_remote import PostgresRemoteDomain
        from plugins.postgres_manager import native_tls
        encrypted = encryption_enabled if encryption_enabled is not None else (True if issue_ssl is None else issue_ssl)
        host, managed = native_tls.build_hostname(mode, domain, subdomain, hostname)
        cidrs = native_tls.normalize_cidrs(allowed_cidrs or ["0.0.0.0/0"])
        domain_id = None
        if managed:
            parent = await db.scalar(select(Domain).where(Domain.name == domain.strip().lower()))
            if not parent:
                raise ValueError("Managed parent domain is not registered.")
            domain_id = parent.id
            from services import dns_service
            import config
            await dns_service.add_a_record(parent.name, subdomain.strip().lower(), config.SERVER_IP, 300)
        if encrypted:
            addresses = await native_tls.resolve_host(host)
            import config
            if config.SERVER_IP not in ("127.0.0.1", "localhost") and config.SERVER_IP not in addresses:
                raise ValueError("Hostname must point to this server before SSL is enabled.")
        existing = await db.scalar(select(PostgresRemoteDomain).where(PostgresRemoteDomain.full_domain == host))
        if existing:
            raise ValueError("This hostname is already configured.")
        record = PostgresRemoteDomain(domain_id=domain_id, mode=mode, subdomain=subdomain if managed else None,
            full_domain=host, encryption_enabled=encrypted, allowed_cidrs=",".join(cidrs),
            nginx_stream=False, dns_status="ready", tls_status="pending" if encrypted else "disabled", postgres_status="pending")
        db.add(record)
        await db.flush()
        try:
            rows = list((await db.scalars(select(PostgresRemoteDomain))).all())
            active = [row for row in rows if row.enabled]
            encrypted_hosts = [row.full_domain for row in active if row.encryption_enabled]
            if encrypted:
                encrypted_hosts.append(host)
            if encrypted_hosts:
                from services import nginx_service
                nginx_service.ensure_acme_root()
                for certificate_host in encrypted_hosts:
                    nginx_service.create_webroot(certificate_host)
                    await nginx_service.create_static_site(certificate_host)
                await nginx_service.reload()
                cert_name, expiry = await native_tls.issue_shared_certificate(encrypted_hosts)
                for row in active:
                    if row.encryption_enabled:
                        row.certificate_name, row.certificate_expiry, row.ssl_active = cert_name, expiry, True
                record.certificate_name, record.certificate_expiry, record.ssl_active = cert_name, expiry, True
            enabled_rows = active + [record]
            await native_tls.configure_postgres(enabled_rows)
            await native_tls.firewall_allow(sorted({cidr for row in enabled_rows for cidr in row.allowed_cidrs.split(',') if cidr}))
            record.enabled, record.postgres_status, record.tls_status, record.last_error = True, "ready", "ready" if encrypted else "disabled", None
            await db.commit()
        except Exception as exc:
            record.last_error, record.postgres_status, record.tls_status = str(exc), "error", "error" if encrypted else "disabled"
            await db.commit()
            raise ValueError(str(exc)) from exc
        return native_tls.endpoint_state(record)

    async def reissue_remote_ssl(self, db: AsyncSession, full_host: str) -> dict[str, Any]:
        from sqlalchemy import select
        from models.postgres_remote import PostgresRemoteDomain
        from plugins.postgres_manager import native_tls
        record = await db.scalar(select(PostgresRemoteDomain).where(PostgresRemoteDomain.full_domain == full_host))
        if not record:
            raise ValueError("Endpoint not found.")
        if not record.encryption_enabled:
            await native_tls.resolve_host(record.full_domain)
            record.encryption_enabled = True
        rows = list((await db.scalars(select(PostgresRemoteDomain).where(PostgresRemoteDomain.enabled.is_(True)))).all())
        if record not in rows:
            rows.append(record)
        hosts = [row.full_domain for row in rows if row.encryption_enabled]
        try:
            from services import nginx_service
            nginx_service.ensure_acme_root()
            for certificate_host in hosts:
                nginx_service.create_webroot(certificate_host)
                await nginx_service.create_static_site(certificate_host)
            await nginx_service.reload()
            cert_name, expiry = await native_tls.issue_shared_certificate(hosts)
            await native_tls.configure_postgres(rows)
            for row in rows:
                if row.encryption_enabled:
                    row.certificate_name, row.certificate_expiry, row.ssl_active, row.tls_status = cert_name, expiry, True, "ready"
            await db.commit()
        except Exception as exc:
            record.encryption_enabled, record.ssl_active, record.tls_status, record.last_error = False, False, "error", str(exc)
            await db.commit()
            raise ValueError(str(exc)) from exc
        return native_tls.endpoint_state(record)

    async def test_remote_domain(self, db: AsyncSession, full_host: str) -> dict[str, Any]:
        from sqlalchemy import select
        from models.postgres_remote import PostgresRemoteDomain
        from plugins.postgres_manager import native_tls
        record = await db.scalar(select(PostgresRemoteDomain).where(PostgresRemoteDomain.full_domain == full_host))
        if not record:
            raise ValueError("Endpoint not found.")
        if not record.enabled:
            raise ValueError("Endpoint is not active yet.")
        from dependencies import dependency_manager
        status = dependency_manager.get_status("postgresql") or {}
        if not status.get("running") or not status.get("healthy"):
            raise ValueError("PostgreSQL is not running or port 5432 is not available locally.")
        if record.encryption_enabled:
            await native_tls.resolve_host(record.full_domain)
        return native_tls.endpoint_state(record)

    async def delete_remote_domain(self, db: AsyncSession, full_host: str) -> bool:
        from sqlalchemy import select
        from models.domain import Domain
        from models.postgres_remote import PostgresRemoteDomain
        from plugins.postgres_manager import native_tls
        record = await db.scalar(select(PostgresRemoteDomain).where(PostgresRemoteDomain.full_domain == full_host))
        if not record:
            raise ValueError("Endpoint not found.")
        old_cidrs = set((record.allowed_cidrs or "").split(',')) if record.allowed_cidrs else set()
        if record.mode == "managed" and record.domain_id and record.subdomain:
            parent = await db.get(Domain, record.domain_id)
            if parent:
                from services import dns_service
                try:
                    await dns_service.delete_record(parent.name, record.subdomain, "A")
                except Exception as exc:
                    logger.warning("Could not delete DNS record for %s: %s", full_host, exc)
        await db.delete(record)
        await db.commit()
        rows = list((await db.scalars(select(PostgresRemoteDomain).where(PostgresRemoteDomain.enabled.is_(True)))).all())
        retained = {cidr for row in rows for cidr in (row.allowed_cidrs or "").split(',') if cidr}
        try:
            await native_tls.firewall_remove(sorted(old_cidrs - retained))
        except Exception as exc:
            logger.warning("Could not update firewall rules during deletion: %s", exc)
        try:
            if rows:
                await native_tls.configure_postgres(rows)
            else:
                await native_tls.disable_remote_postgres()
        except Exception as exc:
            logger.warning("Could not update postgres config during deletion: %s", exc)
        return True

postgres_service = PostgresService()
postgres_manager_service = postgres_service
