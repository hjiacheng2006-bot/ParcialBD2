import sys
import unittest
from unittest.mock import MagicMock

# Mocking mysql.connector and dotenv before importing backend modules
class MockSQLError(Exception):
    def __init__(self, msg="Database error", errno=1644):
        super().__init__(msg)
        self.msg = msg
        self.errno = errno

mock_mysql = MagicMock()
mock_mysql_connector = MagicMock()
mock_mysql_connector.Error = MockSQLError
mock_mysql.connector = mock_mysql_connector
sys.modules['mysql'] = mock_mysql
sys.modules['mysql.connector'] = mock_mysql_connector

mock_dotenv = MagicMock()
mock_dotenv.load_dotenv = MagicMock()
sys.modules['dotenv'] = mock_dotenv

sys.path.insert(0, 'backend')
from app import app

class TestERPApi(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.headers = {"Authorization": "Bearer erp_token_1_test"}

    def test_routes_registered(self):
        routes = [rule.rule for rule in app.url_map.iter_rules()]
        self.assertIn('/', routes)
        self.assertIn('/api/health', routes)
        self.assertIn('/api/productos/', routes)
        self.assertIn('/api/productos/<int:id_producto>', routes)
        self.assertIn('/api/terceros/', routes)
        self.assertIn('/api/terceros/<int:id_tercero>', routes)
        self.assertIn('/api/ventas/', routes)
        self.assertIn('/api/ventas/<int:id_venta>', routes)
        self.assertIn('/api/ventas/<int:id_venta>/anular', routes)
        # Punto 2 routes
        self.assertIn('/api/reportes/top-productos', routes)
        self.assertIn('/api/reportes/inventario-critico', routes)
        self.assertIn('/api/reportes/ventas-mensuales', routes)
        # Modulos adicionales: Auth y Cartera
        self.assertIn('/api/auth/login', routes)
        self.assertIn('/api/auth/usuarios', routes)
        self.assertIn('/api/cartera/', routes)
        self.assertIn('/api/cartera/<int:id_venta>/abono', routes)

    def test_frontend_index_served(self):
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'<!DOCTYPE html>', res.data)
        self.assertIn(b'ERP Core', res.data)
        # Test CORS headers
        self.assertEqual(res.headers.get('Access-Control-Allow-Origin'), '*')

    def test_api_health_endpoint(self):
        res = self.client.get('/api/health')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data['estado'], "Servidor Activo")
        self.assertIn("productos", data['endpoints'])
        self.assertIn("reportes", data['endpoints'])

    def test_crear_producto_validation(self):
        res = self.client.post('/api/productos/', json={})
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertIn("error", data)

    def test_crear_tercero_validation(self):
        res = self.client.post('/api/terceros/', json={"nombre": "Solo Nombre"})
        self.assertEqual(res.status_code, 400)

    @unittest.mock.patch('config.get_db_connection')
    def test_reactivar_tercero(self, mock_conn):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id_tercero": 2, "nombre": "Cliente Inactivo", "estado": 0}
        mock_conn.return_value.cursor.return_value = mock_cursor

        res = self.client.put('/api/terceros/2/activar')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("reactivado exitosamente", data["mensaje"])
        self.assertEqual(data["estado"], 1)

    def test_login_requerido_sin_token(self):
        # Punto 4.1: Endpoints protegidos sin token retornan 401
        res = self.client.post('/api/ventas/', json={"id_usuario": 1})
        self.assertEqual(res.status_code, 401)
        res_anular = self.client.put('/api/ventas/1/anular')
        self.assertEqual(res_anular.status_code, 401)

    def test_crear_venta_validation(self):
        res = self.client.post('/api/ventas/', json={"id_usuario": 1}, headers=self.headers)
        self.assertEqual(res.status_code, 400)

        # Empty detalles list
        res2 = self.client.post('/api/ventas/', json={"id_usuario": 1, "id_tercero": 2, "detalles": []}, headers=self.headers)
        self.assertEqual(res2.status_code, 400)

    def test_anular_venta_not_found(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None # Venta no existe

        with unittest.mock.patch('routes.ventas.get_db_connection', return_value=mock_conn):
            res = self.client.put('/api/ventas/999/anular', headers=self.headers)
            self.assertEqual(res.status_code, 404)
            data = res.get_json()
            self.assertIn("no encontrada", data['error'])

    def test_anular_venta_success(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {"id_venta": 1, "estado_venta": "Pendiente"}

        with unittest.mock.patch('routes.ventas.get_db_connection', return_value=mock_conn):
            res = self.client.put('/api/ventas/1/anular', headers=self.headers)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertIn("anulada exitosamente", data['mensaje'])
            self.assertEqual(data['nuevo_estado'], "Anulado")
            mock_conn.commit.assert_called_once()

    def test_anular_venta_trigger_error(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {"id_venta": 1, "estado_venta": "Anulado"}
        
        # Simula error de trigger de MySQL al intentar anular una venta ya anulada
        mock_cursor.execute.side_effect = [
            None, # First execute: SELECT check
            MockSQLError("Error: Una venta anulada no puede volver a cambiarse de estado por seguridad contable.", 1644)
        ]

        with unittest.mock.patch('routes.ventas.get_db_connection', return_value=mock_conn):
            res = self.client.put('/api/ventas/1/anular', headers=self.headers)
            self.assertEqual(res.status_code, 400)
            data = res.get_json()
            self.assertIn("error_db", data)
            self.assertEqual(data['codigo'], 1644)
            mock_conn.rollback.assert_called_once()

    # TESTS PUNTO 2: REPORTES Y ANALÍTICA AVANZADA
    def test_reporte_top_productos(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            {"id_producto": 1, "nombre": "Teclado RGB", "total_unidades_vendidas": 15, "total_recaudado": 3300000.0}
        ]

        with unittest.mock.patch('routes.reportes.get_db_connection', return_value=mock_conn):
            res = self.client.get('/api/reportes/top-productos')
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]['nombre'], "Teclado RGB")

    def test_reporte_inventario_critico(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            {"id_producto": 2, "nombre": "Mouse Gamer", "cantidad_stock": 2, "nivel_minimo": 5, "deficit_unidades": 3}
        ]

        with unittest.mock.patch('routes.reportes.get_db_connection', return_value=mock_conn):
            res = self.client.get('/api/reportes/inventario-critico')
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]['deficit_unidades'], 3)

    def test_reporte_ventas_mensuales(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            {"mes_numero": 10, "mes_nombre": "Octubre", "total_transacciones": 8, "total_ingresos": 12500000.0}
        ]

        with unittest.mock.patch('routes.reportes.get_db_connection', return_value=mock_conn):
            res = self.client.get('/api/reportes/ventas-mensuales')
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]['mes_nombre'], "Octubre")

    # TESTS PUNTO 3: FILTROS DINÁMICOS Y PAGINACIÓN
    def test_productos_filtro_dinamico_like(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            {"id_producto": 1, "nombre": "Teclado Mecanico RGB", "cantidad_stock": 20}
        ]

        with unittest.mock.patch('routes.productos.get_db_connection', return_value=mock_conn):
            res = self.client.get('/api/productos/?nombre=teclado')
            self.assertEqual(res.status_code, 200)
            # Verify query and parameters used LIKE %s
            query, params = mock_cursor.execute.call_args[0]
            self.assertIn("LIKE %s", query)
            self.assertIn("%teclado%", params)

    def test_productos_paginacion(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        with unittest.mock.patch('routes.productos.get_db_connection', return_value=mock_conn):
            res = self.client.get('/api/productos/?page=2&limit=5')
            self.assertEqual(res.status_code, 200)
            query, params = mock_cursor.execute.call_args[0]
            self.assertIn("LIMIT %s OFFSET %s", query)
            self.assertEqual(params, (5, 5)) # page 2 with limit 5 has offset 5

    # TESTS MODIFICAR ESTADO DE VENTA
    def test_modificar_estado_venta_invalido(self):
        res = self.client.put('/api/ventas/1/estado', json={"estado_venta": "EstadoInvalido"})
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertIn("Estado no válido", data['error'])

    def test_modificar_estado_venta_success(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {"id_venta": 1, "estado_venta": "Pendiente", "total_venta": 50000.0}

        with unittest.mock.patch('routes.ventas.get_db_connection', return_value=mock_conn):
            res = self.client.put('/api/ventas/1/estado', json={"estado_venta": "Pagado"})
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data['nuevo_estado'], "Pagado")
            mock_conn.commit.assert_called_once()

    # TESTS AGREGAR PRODUCTOS A VENTA PENDIENTE
    def test_agregar_producto_venta_no_pendiente(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {"id_venta": 1, "estado_venta": "Pagado", "total_venta": 100000.0}

        with unittest.mock.patch('routes.ventas.get_db_connection', return_value=mock_conn):
            res = self.client.post('/api/ventas/1/productos', json={"id_producto": 1, "cantidad": 2})
            self.assertEqual(res.status_code, 400)
            data = res.get_json()
            self.assertIn("Solo se permite agregar productos a ventas en espera", data['error'])

    def test_agregar_producto_venta_pendiente_success(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.lastrowid = 15
        # 1st fetchone: check sale state
        # 2nd fetchone: check updated total
        mock_cursor.fetchone.side_effect = [
            {"id_venta": 1, "estado_venta": "Pendiente", "total_venta": 100000.0},
            {"total_venta": 150000.0, "estado_venta": "Pendiente"}
        ]

        with unittest.mock.patch('routes.ventas.get_db_connection', return_value=mock_conn):
            res = self.client.post('/api/ventas/1/productos', json={
                "id_producto": 2,
                "cantidad": 1,
                "precioUnitario": 50000.0
            })
            self.assertEqual(res.status_code, 201)
            data = res.get_json()
            self.assertEqual(data['id_detalle_venta'], 15)
            self.assertEqual(data['nuevo_total_venta'], 150000.0)
            mock_conn.commit.assert_called_once()

    # TESTS PRIVACIDAD Y SANITIZACIÓN DE ERRORES DE BASE DE DATOS
    def test_error_privacidad_foreign_key(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        # Simula error técnico con nombres internos de tablas, esquemas y constraints
        mock_cursor.execute.side_effect = MockSQLError(
            "Cannot add or update a child row: a foreign key constraint fails (`erp`.`ventas`, CONSTRAINT `fk_ventas_usuarios1` FOREIGN KEY (`id_usuario`) REFERENCES `usuarios` (`id_usuario`))",
            1452
        )

        with unittest.mock.patch('routes.ventas.get_db_connection', return_value=mock_conn):
            res = self.client.post('/api/ventas/', headers=self.headers, json={
                "id_usuario": 999,
                "id_tercero": 1,
                "detalles": [{"id_producto": 1, "cantidad": 1, "precioUnitario": 1000}]
            })
            self.assertEqual(res.status_code, 400)
            data = res.get_json()
            # Validar que NO filtre nombres internos de BD, esquemas o restricciones
            self.assertNotIn("fk_ventas_usuarios1", data['error'])
            self.assertNotIn("erp", data['error'])
            self.assertNotIn("CONSTRAINT", data['error'])
            self.assertIn("registro relacionado no existe", data['error'])

    def test_error_privacidad_duplicado(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = MockSQLError("Duplicate entry 'correo@test.com' for key 'usuarios.correo_UNIQUE'", 1062)

        with unittest.mock.patch('routes.terceros.get_db_connection', return_value=mock_conn):
            res = self.client.post('/api/terceros/', json={
                "nombre": "Test",
                "correo": "correo@test.com",
                "contacto": "123"
            })
            self.assertEqual(res.status_code, 400)
            data = res.get_json()
            self.assertNotIn("correo_UNIQUE", data['error'])
            self.assertIn("valor duplicado", data['error'])

    # TESTS MODULOS ADICIONALES: AUTENTICACIÓN Y CARTERA
    def test_auth_login_success(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {
            "id_usuario": 1,
            "nombre": "Administrador",
            "correo": "admin@erp.com",
            "estado": 1,
            "roles_id_rol": 1
        }

        with unittest.mock.patch('routes.auth.get_db_connection', return_value=mock_conn):
            res = self.client.post('/api/auth/login', json={
                "correo": "admin@erp.com",
                "password": "admin123"
            })
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertIn("usuario", data)
            self.assertEqual(data['usuario']['correo'], "admin@erp.com")

    def test_auth_login_invalid(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None

        with unittest.mock.patch('routes.auth.get_db_connection', return_value=mock_conn):
            res = self.client.post('/api/auth/login', json={
                "correo": "admin@erp.com",
                "password": "password_incorrecta"
            })
            self.assertEqual(res.status_code, 401)
            data = res.get_json()
            self.assertIn("Credenciales inválidas", data['error'])

    def test_cartera_listar(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            {"id_venta": 5, "total_venta": 150000.0, "estado_venta": "Por Pagar", "nombre_cliente": "Juan Perez"}
        ]

        with unittest.mock.patch('routes.cartera.get_db_connection', return_value=mock_conn):
            res = self.client.get('/api/cartera/')
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertIn("cuentas_por_cobrar", data)
            self.assertEqual(data['total_cartera_activa'], 150000.0)

    def test_cartera_abono_success(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {
            "id_venta": 5,
            "total_venta": 100000.0,
            "estado_venta": "Por Pagar"
        }

        with unittest.mock.patch('routes.cartera.get_db_connection', return_value=mock_conn):
            res = self.client.post('/api/cartera/5/abono', json={"monto": 100000.0})
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data['nuevo_estado'], "Pagado")
            mock_conn.commit.assert_called_once()

    def test_subir_imagen_producto_success(self):
        # Punto 4.4: Subida de imágenes usando request.files
        import io
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {"id_producto": 1, "nombre": "Teclado RGB"}

        data = {
            'imagen': (io.BytesIO(b"fake-image-bytes"), 'teclado.png')
        }
        with unittest.mock.patch('routes.productos.get_db_connection', return_value=mock_conn):
            res = self.client.post('/api/productos/1/imagen', data=data, content_type='multipart/form-data')
            self.assertEqual(res.status_code, 200)
            res_data = res.get_json()
            self.assertIn("imagen_url", res_data)
            self.assertTrue(res_data['imagen_url'].startswith('/uploads/'))
            mock_conn.commit.assert_called_once()

    def test_soft_delete_cascada_trigger_error(self):
        # Punto 4.3: Soft Delete en cascada con ventas_BEFORE_INSERT
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = MockSQLError("Operación denegada: El cliente seleccionado se encuentra inactivo (Soft Delete).", 1644)

        with unittest.mock.patch('routes.ventas.get_db_connection', return_value=mock_conn):
            res = self.client.post('/api/ventas/', headers=self.headers, json={
                "id_usuario": 1,
                "id_tercero": 99, # Cliente inactivo
                "detalles": [{"id_producto": 1, "cantidad": 1, "precioUnitario": 1000}]
            })
            self.assertEqual(res.status_code, 400)
            data = res.get_json()
            self.assertIn("error_db", data)
            self.assertEqual(data['codigo'], 1644)

if __name__ == '__main__':
    unittest.main()
