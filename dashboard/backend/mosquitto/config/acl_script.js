bridge_username = "bridge_username";
bridge_password = "bridge_password";

function checkAcl(username, topic, clientid, acc) {
    console.log("acl_script.js: username: " + username + ", topic: " + topic + ", clientid: " + clientid + ", acc: " + acc);

    // if topic starts with username, allow access
    // can't use .startsWith() since using otto
    if (topic.indexOf(username) === 0) {
        return true;
    }

    if (username === "N0CALL") {
        return false; // deny access to all topics for N0CALL

        // allow read-only access to all topics for N0CALL
        if (acc === 1 || acc === 4) {
            return true;
        } else {
            return false;
        }
    }

    if (username === bridge_username) {
        // allow access to all topics for bridge username
        return true;
    }

    return false;
}

checkAcl(username, topic, clientid, acc);
