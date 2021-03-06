import json
from datetime import timedelta
import import_stuff

from flask import Flask, render_template, request, jsonify
from flask_mysqldb import MySQL

import re
from flask_jwt_extended import (
    JWTManager, jwt_required, create_access_token,
    get_jwt_identity, set_access_cookies, unset_jwt_cookies,
)

from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
jwt = JWTManager(app)
mysql = MySQL(app)


app.config['JWT_TOKEN_LOCATION'] = ['cookies']
app.config['JWT_SECRET_KEY'] = import_stuff.get_keys()
app.config['JWT_COOKIE_SECURE'] = False
app.config['JWT_COOKIE_CSRF_PROTECT'] = False
app.config['JWT_ACCESS_COOKIE_PATH'] = '/home'
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=2)

app.config['MYSQL_HOST'] = ''
app.config['MYSQL_USER'] = ''
app.config['MYSQL_PASSWORD'] = ''
app.config['MYSQL_DB'] = ''


@app.route('/signup', methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.json.get('name', None)
        if not re.match('[A-Za-z]*$', name):
            return jsonify({"msg": "Your name can only contain literals"}), 401
        else:
            user_name = name

        email = request.json.get('email', None)
        if not re.match('^[_A-Za-z0-9-\\+]+(\\.[_A-Za-z0-9-]+)*@[A-Za-z0-9-]+(\\.[A-Za-z0-9]+)*(\\.[A-Za-z]{2,})$',
                        email):
            return jsonify({"msg": "Please, enter a valid email"}), 401
        else:
            user_email = email

        password = request.json.get('password', None)

        if not re.match(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!?:@#$*%^&_+.])[A-Za-z\d!?:@#$*%^&_+.]{8,20}$",password):
            return jsonify({"msg": "Please, enter a valid password"}), 401
        else:
            user_password = password

        confirm = request.json.get('confirm', None)

        cur = mysql.connection.cursor()

        if user_password == confirm:
            query_email = "SELECT EXISTS(SELECT email FROM user WHERE email = '%(email)s') " % {"email": user_email}
            cur.execute(query_email)
            check_email = cur.fetchone()

            if check_email[0] == 0:
                secure_password = generate_password_hash(user_password, "sha256", 12)
                query_insert = ("""INSERT INTO user(name, email, password)
                                VALUES ('%(name)s', '%(email)s', '%(password)s')""" %
                                {"email": user_email, "name": user_name, "password": secure_password})

                cur.execute(query_insert)
                mysql.connection.commit()

                return jsonify({'signup': True})

            else:
                return jsonify({"msg": "This e-mail is already in use"}), 401
        else:
            return jsonify({"msg": "Passwords do not match"}), 401

    return render_template('signup.html')


@app.route('/login', methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.json.get('email', None)
        if not re.match('^[_A-Za-z0-9-\\+]+(\\.[_A-Za-z0-9-]+)*@[A-Za-z0-9-]+(\\.[A-Za-z0-9]+)*(\\.[A-Za-z]{2,})$',
                        email):
            return jsonify({"msg": "Please, enter a valid email"}), 401
        else:
            user_email = email

        password = request.json.get('password', None)

        cur = mysql.connection.cursor()
        check_email = "SELECT EXISTS(SELECT email FROM user WHERE email = '%(email)s') " % {"email": user_email}
        cur.execute(check_email)
        check_email = cur.fetchone()

        if check_email[0] == 0:
            return jsonify({"msg": "Incorrect credentials"}), 401
        else:
            query = "SELECT * FROM user WHERE email = '%(email)s' " % {"email": user_email}
            cur.execute(query)
            data = cur.fetchone()

            if check_password_hash(data[3], password):
                access_token = create_access_token(identity=data[0])
                resp = jsonify({'login': True})
                set_access_cookies(resp, access_token)
                return resp
            else:
                return jsonify({"msg": "Incorrect credentials"}), 401

    return render_template('login.html')


@app.route('/logout')
def logout():
    resp = jsonify({'logout': True})
    unset_jwt_cookies(resp)
    return resp, 200


@jwt.expired_token_loader
def my_expired_token_callback(callback):
    return render_template('redirect.html')


@jwt.unauthorized_loader
def unauthorized_loader_handler(callback):
    return render_template('redirect.html')


@app.route('/home', methods=['GET', 'POST'])
@jwt_required
def home():
    current_user = get_jwt_identity()

    cursor = mysql.connection.cursor()

    chosen_filters = request.args.to_dict(flat=False)

    if chosen_filters == {}:
        data = recommendations(current_user, cursor)
    else:

        cuisines_option_query = cuisines_option(chosen_filters)

        location_option_query = location_option(cuisines_option_query, chosen_filters)

        cursor.execute(additional_options(location_option_query, chosen_filters))
        data = cursor.fetchall()

    new_data = []
    for i in data:
        new_data.append(list(i))

    for i in new_data:
        i[6] = json.loads(i[6])
        i[15] = json.loads(i[15])

    like_list = check_liked_restaurants(current_user, cursor)

    if request.method == "POST":
        like_and_unlike(current_user, cursor)

    return render_template('index.html', data=new_data, like_list=like_list)


def additional_options(query, filters):
    additional_filters = ['vegetarian', 'vegan', 'gluten_free', 'credit_card', 'takeaway']
    for filter_name in additional_filters:
        if filter_name in filters:
            query = "SELECT * FROM (%s) as t WHERE %s=1 " % (query, filter_name)
        else:
            query = "SELECT * FROM (%s) as t" % query

    return query


def cuisines_option(filters):
    query_fragment = """SELECT r.id, r.name, r.address, r.image, r.rating, r.price_range, r.timing,
                        r.vegan, r.vegetarian, r.gluten_free, r.credit_card, r.takeaway,
                        r.phone_num1, r.phone_num2, r.usual_menu_url, r.menu,
                        r.latitute, r.longitude, GROUP_CONCAT(c.name separator ', ') as cuisines
                        FROM restaurant r LEFT JOIN
                        restaurant_cuisine rc
                        ON rc.restaurant_id = r.id LEFT JOIN
                        cuisines c
                        ON c.id = rc.cuisine_id"""

    if 'cuisines' in filters:
        cuisines = tuple(filters.get('cuisines'))

        if len(cuisines) == 1:
            filtered_by_cuisine = ("%s WHERE c.name = '%s' GROUP BY r.id " % (query_fragment, cuisines[0]))

        else:
            filtered_by_cuisine = ("%s WHERE c.name in %s GROUP BY r.id " % (query_fragment, cuisines))

    else:
        filtered_by_cuisine = ("%s GROUP BY r.id " % query_fragment)

    return filtered_by_cuisine


def location_option(cuisine_query, filters):
    query_fragment = """id, name, address, image, rating, price_range, timing,
                vegan, vegetarian, gluten_free, credit_card, takeaway,
                phone_num1, phone_num2, usual_menu_url, menu, latitute,
                longitude"""

    if 'lat' and 'lon' in filters:

        lat = filters.get('lat')[0]
        lon = filters.get('lon')[0]

        formula = ("""(6371 * acos(cos( radians(%s
                )) * cos( radians( latitute )) * cos( radians( longitude ) - radians(%s
                ) ) + sin( radians(%s
                ) ) * sin( radians( latitute ))))""" % (lat, lon, lat))

        filter_by_distance = ("""SELECT %s, cuisines,
                   FORMAT(%s, 2) AS distance,
                   FORMAT((%s * 12), 1) AS time
                   FROM (%s) as t HAVING distance < 3 ORDER
                   BY distance""" % (query_fragment, formula, formula, cuisine_query))

    else:
        filter_by_distance = ("SELECT * FROM (%s) as t" % cuisine_query)

    return filter_by_distance


def recommendations(user_id, cur):
    check_user = """SELECT EXISTS(SELECT user_id FROM restaurant_user WHERE user_id = %s)""" % user_id
    cur.execute(check_user)
    check_user_result = cur.fetchone()

    top_restaurants_query = """SELECT r.id, r.name, r.address, r.image, r.rating, r.price_range, r.timing,
                                        r.vegan, r.vegetarian, r.gluten_free, r.credit_card, r.takeaway,
                                        r.phone_num1, r.phone_num2, r.usual_menu_url, r.menu,
                                        r.latitute, r.longitude, GROUP_CONCAT(c.name separator ', ') as cuisines
                                        FROM restaurant r LEFT JOIN
                                        restaurant_cuisine rc ON rc.restaurant_id = r.id LEFT JOIN
                                        cuisines c ON c.id = rc.cuisine_id
                                        GROUP BY r.id ORDER BY rating DESC"""

    if check_user_result[0] == 0:
        cur.execute(top_restaurants_query)
        top_restaurants_result = cur.fetchall()
        return top_restaurants_result

    else:
        liked_restaurants_query = """SELECT restaurant_id FROM restaurant_user WHERE user_id = %s""" % user_id

        cuisines_query = """SELECT cuisines.name FROM restaurant_cuisine JOIN restaurant
                            ON restaurant.id = restaurant_cuisine.restaurant_id JOIN cuisines
                            ON cuisines.id = restaurant_cuisine.cuisine_id WHERE restaurant.id in (%s)
                            GROUP BY cuisines.name
                            ORDER BY COUNT(*) DESC
                            LIMIT 3""" % liked_restaurants_query

        cur.execute(cuisines_query)
        cuisines_result = cur.fetchall()

        liked_cuisines = []

        for tuple_cuisine in cuisines_result:
            for cuisine in tuple_cuisine:
                liked_cuisines.append(cuisine)
        liked_cuisines = tuple(liked_cuisines)

        other_filters_query = """SELECT vegan, vegetarian, gluten_free, price_range
                                FROM restaurant WHERE id in (%s)
                                GROUP BY vegan, vegetarian, gluten_free, price_range
                                ORDER BY COUNT(*) DESC LIMIT 1""" % liked_restaurants_query

        cur.execute(other_filters_query)
        other_result = cur.fetchall()

        if len(liked_cuisines) == 1:
            recommended_restaurants_query = """SELECT r.id, r.name, r.address, r.image,
                                    r.rating, r.price_range, r.timing,
                                    r.vegan, r.vegetarian, r.gluten_free, r.credit_card, r.takeaway,
                                    r.phone_num1, r.phone_num2, r.usual_menu_url, r.menu,
                                    GROUP_CONCAT(c.name separator ', ') as cuisines
                                    FROM restaurant r LEFT JOIN
                                    restaurant_cuisine rc
                                    ON rc.restaurant_id = r.id LEFT JOIN
                                    cuisines c
                                    ON c.id = rc.cuisine_id
                                    WHERE c.name = '%s' AND vegan = %s AND vegetarian = %s
                                    AND gluten_free = %s AND price_range = %s
                                    GROUP BY r.id
                                    """ % (liked_cuisines[0], other_result[0][0],
                                           other_result[0][1], other_result[0][2], other_result[0][3])
        else:
            recommended_restaurants_query = """SELECT r.id, r.name, r.address, r.image,
                                    r.rating, r.price_range, r.timing,
                                    r.vegan, r.vegetarian, r.gluten_free, r.credit_card, r.takeaway,
                                    r.phone_num1, r.phone_num2, r.usual_menu_url, r.menu,
                                    GROUP_CONCAT(c.name separator ', ') as cuisines
                                    FROM restaurant r LEFT JOIN
                                    restaurant_cuisine rc
                                    ON rc.restaurant_id = r.id LEFT JOIN
                                    cuisines c
                                    ON c.id = rc.cuisine_id
                                    WHERE c.name in %s AND vegan = %s AND vegetarian = %s
                                    AND gluten_free = %s AND price_range = %s
                                    GROUP BY r.id
                                    """ % (liked_cuisines, other_result[0][0],
                                           other_result[0][1], other_result[0][2], other_result[0][3])

            cur.execute(recommended_restaurants_query)
        recommended_data = cur.fetchall()

        if len(recommended_data) <= 3:
            cur.execute(top_restaurants_query)
            top_restaurants_result = cur.fetchall()
            return top_restaurants_result
        else:
            return recommended_data


def check_liked_restaurants(user_id, cur):
    liked_restaurants = []
    check_user_query = """SELECT EXISTS(SELECT user_id FROM restaurant_user WHERE user_id = %s)""" % user_id
    cur.execute(check_user_query)
    check_user_result = cur.fetchone()

    if check_user_result[0] == 0:
        return liked_restaurants
    else:
        liked_restaurants_query = """SELECT restaurant_id FROM restaurant_user WHERE user_id = %s""" % user_id
        cur.execute(liked_restaurants_query)
        liked_restaurants_result = cur.fetchall()
        for rest_id in liked_restaurants_result:
            liked_restaurants.append(rest_id[0])
        return liked_restaurants


def like_and_unlike(user_id, cur):
    rest_id = request.json.get('restId', None)

    check_if_liked_query = "SELECT EXISTS(SELECT * FROM restaurant_user WHERE user_id = %s AND restaurant_id = %s ) " \
                           % (user_id, rest_id)

    cur.execute(check_if_liked_query)
    check_result = cur.fetchone()
    if check_result[0] == 0:
        query = "INSERT INTO restaurant_user (restaurant_id, user_id) VALUES (%s, %s)" % (rest_id, user_id)

    else:
        query = "DELETE FROM restaurant_user WHERE restaurant_id = %s AND user_id = %s " % (rest_id, user_id)

    cur.execute(query)
    mysql.connection.commit()


if __name__ == "__main__":
    app.run()

