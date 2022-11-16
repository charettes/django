from functools import lru_cache

from django.db.backends.base.base import NO_DB_ALIAS
from django.db.backends.postgresql.base import DatabaseWrapper as PsycopgDatabaseWrapper
from django.db.backends.postgresql.psycopg_any import is_psycopg3

from .adapter import PostGISAdapter
from .features import DatabaseFeatures
from .introspection import PostGISIntrospection
from .operations import PostGISOperations
from .schema import PostGISSchemaEditor

if is_psycopg3:
    from psycopg.adapt import Dumper, Loader
    from psycopg.pq import Format

    class GeometryType:
        pass

    class GeographyType:
        pass

    class RasterType:
        pass

    class BinaryLoader(Loader):
        format = Format.BINARY

        def load(self, data):
            if not isinstance(data, bytes):
                data = bytes(data)
            return data

    class TextLoader(Loader):
        def load(self, data):
            if isinstance(data, memoryview):
                return bytes(data).decode()
            return data.decode()

    @lru_cache
    def postgis_adapters(geo_info, geog_info, raster_info):
        class PostGISTextDumper(Dumper):
            class BaseTextDumper(Dumper):
                def dump(self, obj):
                    # Return bytes as hex for text formatting
                    return obj.ewkb.hex().encode()

            class GeometryTextDumper(BaseTextDumper):
                oid = geo_info.oid if geo_info else None

            class GeographyTextDumper(BaseTextDumper):
                oid = geog_info.oid if geog_info else None

            class RasterTextDumper(BaseTextDumper):
                oid = raster_info.oid if raster_info else None

            def get_key(self, obj, format):
                if obj.is_geometry:
                    return GeographyType if obj.geography else GeometryType
                else:
                    return RasterType

            def upgrade(self, obj, format):
                if obj.is_geometry:
                    if obj.geography:
                        return self.GeographyTextDumper(GeographyType)
                    else:
                        return self.GeometryTextDumper(GeometryType)
                else:
                    return self.RasterTextDumper(RasterType)

            def dump(self, obj):
                raise NotImplementedError("Should not happen")

        class PostGISBinaryDumper(Dumper):
            format = Format.BINARY

            class BaseBinaryDumper(Dumper):
                format = Format.BINARY

                def dump(self, obj):
                    return obj.ewkb

            class GeometryBinaryDumper(BaseBinaryDumper):
                oid = geo_info.oid if geo_info else None

            class GeographyBinaryDumper(BaseBinaryDumper):
                oid = geog_info.oid if geog_info else None

            class RasterDumper(Dumper):
                oid = raster_info.oid if raster_info else None

                def dump(self, obj):
                    return obj.ewkb.hex().encode()

            def get_key(self, obj, format):
                if obj.is_geometry:
                    return GeographyType if obj.geography else GeometryType
                else:
                    return RasterType

            def upgrade(self, obj, format):
                if obj.is_geometry:
                    if obj.geography:
                        return self.GeographyBinaryDumper(GeographyType)
                    else:
                        return self.GeometryBinaryDumper(GeometryType)
                else:
                    return self.RasterDumper(RasterType)

            def dump(self, obj):
                raise NotImplementedError("Should not happen")

        return PostGISTextDumper, PostGISBinaryDumper


class DatabaseWrapper(PsycopgDatabaseWrapper):
    SchemaEditorClass = PostGISSchemaEditor

    _geometry_types = {}
    _geography_types = {}
    _raster_types = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if kwargs.get("alias", "") != NO_DB_ALIAS:
            self.features = DatabaseFeatures(self)
            self.ops = PostGISOperations(self)
            self.introspection = PostGISIntrospection(self)

    def prepare_database(self):
        super().prepare_database()
        # Check that postgis extension is installed.
        with self.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_extension WHERE extname = %s", ["postgis"])
            if bool(cursor.fetchone()):
                return
            cursor.execute("CREATE EXTENSION IF NOT EXISTS postgis")
            self.register_geometry_adapters(self.connection)

    def get_new_connection(self, conn_params):
        connection = super().get_new_connection(conn_params)
        self.register_geometry_adapters(connection)
        return connection

    if is_psycopg3:

        def register_geometry_adapters(self, pg_connection):
            from psycopg.types import TypeInfo

            geo_info = self._geometry_types.get(self.alias)
            if not geo_info:
                geo_info = TypeInfo.fetch(pg_connection, "geometry")
                self._geometry_types[self.alias] = geo_info
            if geo_info:
                geo_info.register(pg_connection)
                pg_connection.adapters.register_loader(geo_info.oid, TextLoader)
                pg_connection.adapters.register_loader(geo_info.oid, BinaryLoader)

            raster_info = self._raster_types.get(self.alias)
            if not raster_info:
                raster_info = TypeInfo.fetch(pg_connection, "raster")
                self._raster_types[self.alias] = raster_info
            if raster_info:
                raster_info.register(pg_connection)
                pg_connection.adapters.register_loader(raster_info.oid, TextLoader)
                pg_connection.adapters.register_loader(raster_info.oid, BinaryLoader)

            geog_info = self._geography_types.get(self.alias)
            if not geog_info:
                geog_info = TypeInfo.fetch(pg_connection, "geography")
                self._geography_types[self.alias] = geog_info
            if geog_info:
                geog_info.register(pg_connection)
                pg_connection.adapters.register_loader(geog_info.oid, TextLoader)
                pg_connection.adapters.register_loader(geog_info.oid, BinaryLoader)

            PostGISTextDumper, PostGISBinaryDumper = postgis_adapters(
                geo_info, geog_info, raster_info
            )
            pg_connection.adapters.register_dumper(PostGISAdapter, PostGISTextDumper)
            pg_connection.adapters.register_dumper(PostGISAdapter, PostGISBinaryDumper)

    else:

        def register_geometry_adapters(self, pg_connection):
            pass
