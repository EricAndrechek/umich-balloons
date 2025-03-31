bridge_username = "bridge_username";
bridge_password = "bridge_password";

function checkSuperuser(username) {
    if (username == "") {
        console.log("Superuser access denies for empty username");
        return false;
    }

    if (bridge_username == username) {
        console.log("Superuser access granted for: " + username); // log superuser access
        return true;
    }

    console.log("Superuser access denied for: " + username); // log superuser access
    return false;
}

checkSuperuser(username);
