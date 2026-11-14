import json
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from flask_compress import Compress
from werkzeug.security import generate_password_hash, check_password_hash
from openai import OpenAI

app = Flask(__name__)
Compress(app)
app.secret_key = 'ecowatt_secret_key_ods7_2026'

# Configuración Base de Datos SQLite
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///ecowatt.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Cliente API para LM Studio / Bionic
client = OpenAI(
    base_url="http://localhost:1234/v1",
    api_key="lm-studio"
)

# --- MODELOS SQL ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)

class Appliance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    name = db.Column(db.String(100), nullable=False)
    power = db.Column(db.Float, nullable=False)
    hours = db.Column(db.Float, nullable=False)

class Bill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    period = db.Column(db.String(50), nullable=False)
    kwh = db.Column(db.Float, nullable=False)
    cost = db.Column(db.Float, nullable=True)

with app.app_context():
    try:
        db.create_all()
    except Exception:
        pass

# FunciÃ³n para borrar cachÃ© cuando cambian los datos
def invalidar_cache_ia():
    session.pop('cached_recommendations', None)

# --- FUNCIÓN CON CACHÉ DE IA PARA NAVEGACIÓN RÁPIDA ---

def obtener_contexto_dashboard():
    user_id = session.get('user_id')
    
    if user_id:
        appliances = Appliance.query.filter_by(user_id=user_id).all()
        bills = Bill.query.filter_by(user_id=user_id).all()
    else:
        appliances = Appliance.query.all()
        bills = Bill.query.all()

    total_est = sum((a.power * a.hours * 30) / 1000 for a in appliances)
    if total_est == 0:
        total_est = 180.5

    if bills:
        ult_factura = bills[-1]
        real_kwh = ult_factura.kwh
        periodo_str = ult_factura.period
    else:
        real_kwh = 220.0
        periodo_str = "Octubre 2026"

    diferencia = round(real_kwh - total_est, 1)
    diff_percent = round((diferencia / total_est) * 100, 1) if total_est > 0 else 0

    nombres_equipos = [a.name for a in appliances] if appliances else []
    
    # USO DE CACHÉ: Si no han cambiado los datos, reutiliza la respuesta anterior instantáneamente
    if 'cached_recommendations' in session:
        recommendations = session['cached_recommendations']
    else:
        lista_equipos_str = ", ".join([f"{a.name} ({a.power}W por {a.hours}h/día)" for a in appliances]) if appliances else "Sin equipos"

        try:
            prompt_imperativo = (
                f"REGLA OBLIGATORIA: Escribe exactamente 3 recomendaciones prácticas de ahorro de energía. "
                f"CADA RECOMENDACIÓN DEBE MENCIONAR POR SU NOMBRE OBLIGATORIAMENTE uno de estos electrodomésticos: {', '.join(nombres_equipos) if nombres_equipos else 'tu consumo'}.\n\n"
                f"DATOS:\n- Aparatos: {lista_equipos_str}\n- Consumo estimado: {total_est} kWh/mes\n- Real factura ({periodo_str}): {real_kwh} kWh\n\n"
                f"Responde directamente con 3 líneas cortas sin texto introductorio."
            )

            response = client.chat.completions.create(
                model="mistral-7b-instruct-v0.1",
                messages=[{"role": "user", "content": prompt_imperativo}],
                temperature=0.2,
                max_tokens=200
            )
            
            raw_text = response.choices[0].message.content.strip().replace("**", "")
            lines = [line.strip().lstrip('-*123456789. ') for line in raw_text.split('\n') if line.strip()]
            recommendations = [line for line in lines if len(line) > 12 and "hola" not in line.lower()][:3]
            
            if not recommendations or not any(nombre.lower() in " ".join(recommendations).lower() for nombre in nombres_equipos):
                raise ValueError("Incompleto")

        except Exception:
            recommendations = []
            for app_obj in appliances:
                recommendations.append(f"Para tu {app_obj.name}: Revisa su estado y desconéctalo cuando no esté en uso para evitar el consumo fantasma.")
            if len(recommendations) < 3:
                recommendations.append(f"Tus equipos registran {total_est} kWh/mes frente a {real_kwh} kWh reales; ajusta los tiempos de uso.")

        session['cached_recommendations'] = recommendations[:3]

    return {
        "total_estimated": round(total_est, 1),
        "real_kwh": real_kwh,
        "difference": diferencia,
        "diff_percent": diff_percent,
        "latest_bill": {"period": periodo_str},
        "recommendations": recommendations,
        "appliances": appliances,
        "bills": bills
    }

# --- RUTAS DE LA APLICACIÓN ---

@app.route('/')
def index():
    datos = obtener_contexto_dashboard()
    return render_template('index.html', **datos)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            session['username'] = user.username
            invalidar_cache_ia()
            flash('¡Sesión iniciada correctamente!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Usuario o contraseña incorrectos.', 'danger')

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if User.query.filter_by(username=username).first():
            flash('El nombre de usuario ya está registrado.', 'warning')
            return redirect(url_for('register'))

        nuevo_usuario = User(username=username, password_hash=generate_password_hash(password))
        db.session.add(nuevo_usuario)
        db.session.commit()

        session['user_id'] = nuevo_usuario.id
        session['username'] = nuevo_usuario.username
        invalidar_cache_ia()
        flash('¡Cuenta creada e inicio de sesión exitoso!', 'success')
        return redirect(url_for('index'))

    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Has cerrado sesión.', 'info')
    return redirect(url_for('index'))

@app.route('/manage_appliances', methods=['GET', 'POST'])
def manage_appliances():
    if request.method == 'POST':
        name = request.form.get('name')
        power = request.form.get('power')
        hours = request.form.get('hours')

        if name and power:
            nuevo = Appliance(user_id=session.get('user_id'), name=name, power=float(power), hours=float(hours) if hours else 1.0)
            db.session.add(nuevo)
            db.session.commit()
            invalidar_cache_ia()
            flash('Electrodoméstico agregado con éxito.', 'success')
        return redirect(url_for('manage_appliances'))

    datos = obtener_contexto_dashboard()
    return render_template('appliances.html', **datos)

@app.route('/edit_appliance/<int:id>', methods=['GET', 'POST'])
def edit_appliance(id):
    appliance = Appliance.query.get_or_404(id)
    if request.method == 'POST':
        appliance.name = request.form.get('name')
        appliance.power = float(request.form.get('power'))
        appliance.hours = float(request.form.get('hours'))
        db.session.commit()
        invalidar_cache_ia()
        flash('Electrodoméstico actualizado correctamente.', 'success')
        return redirect(url_for('manage_appliances'))
    
    datos = obtener_contexto_dashboard()
    datos['editing_appliance'] = appliance
    return render_template('appliances.html', **datos)

@app.route('/delete_appliance/<int:id>')
def delete_appliance(id):
    appliance = Appliance.query.get_or_404(id)
    db.session.delete(appliance)
    db.session.commit()
    invalidar_cache_ia()
    flash('Electrodoméstico eliminado correctamente.', 'info')
    return redirect(url_for('manage_appliances'))

@app.route('/manage_bills', methods=['GET', 'POST'])
def manage_bills():
    if request.method == 'POST':
        period = request.form.get('period')
        kwh = request.form.get('kwh')
        cost = request.form.get('cost')

        if period and kwh:
            nueva = Bill(user_id=session.get('user_id'), period=period, kwh=float(kwh), cost=float(cost) if cost else 0.0)
            db.session.add(nueva)
            db.session.commit()
            invalidar_cache_ia()
            flash('Factura registrada con éxito.', 'success')
        return redirect(url_for('manage_bills'))

    datos = obtener_contexto_dashboard()
    return render_template('bills.html', **datos)

@app.route('/edit_bill/<int:id>', methods=['GET', 'POST'])
def edit_bill(id):
    bill = Bill.query.get_or_404(id)
    if request.method == 'POST':
        bill.period = request.form.get('period')
        bill.kwh = float(request.form.get('kwh'))
        bill.cost = float(request.form.get('cost')) if request.form.get('cost') else 0.0
        db.session.commit()
        invalidar_cache_ia()
        flash('Factura actualizada correctamente.', 'success')
        return redirect(url_for('manage_bills'))

    datos = obtener_contexto_dashboard()
    datos['editing_bill'] = bill
    return render_template('bills.html', **datos)

@app.route('/delete_bill/<int:id>')
def delete_bill(id):
    bill = Bill.query.get_or_404(id)
    db.session.delete(bill)
    db.session.commit()
    invalidar_cache_ia()
    flash('Factura eliminada correctamente.', 'info')
    return redirect(url_for('manage_bills'))

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)