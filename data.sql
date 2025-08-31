
-- ===============================
-- 1. Users Table
-- ===============================
CREATE TABLE Users (
    user_id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(150) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL,
    role ENUM('user','fact-checker','admin') DEFAULT 'user'
);

select * from Users

-- ===============================
-- 2. Sources Table
-- ===============================
CREATE TABLE Sources (
    source_id INT PRIMARY KEY AUTO_INCREMENT,
    source_name VARCHAR(200) UNIQUE NOT NULL,
    credibility_score DECIMAL(5,2) DEFAULT 50.00 CHECK (credibility_score BETWEEN 0 AND 100)
);
ALTER TABLE Articles ADD COLUMN source VARCHAR(50) DEFAULT 'manual';


-- Insert default source to avoid FK errors
INSERT INTO Sources (source_name, credibility_score) VALUES ('Default Source', 80);

select * from Sources
-- ===============================
-- 3. Articles Table
-- ===============================
CREATE TABLE Articles (
    article_id INT PRIMARY KEY AUTO_INCREMENT,
    title VARCHAR(300) NOT NULL,
    content TEXT NOT NULL,
    url VARCHAR(500),
    publish_date DATE,
    source_id INT,
    trust_score DECIMAL(5,2) DEFAULT 50.00 CHECK (trust_score BETWEEN 0 AND 100),
    FOREIGN KEY (source_id) REFERENCES Sources(source_id)
);

select * from Articles

-- ===============================
-- 4. Reports Table
-- ===============================
CREATE TABLE Reports (
    report_id INT PRIMARY KEY AUTO_INCREMENT,
    article_id INT NOT NULL,
    user_id INT NOT NULL,
    reason ENUM('fake','misleading','spam','other') NOT NULL,
    report_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (article_id) REFERENCES Articles(article_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES Users(user_id) ON DELETE CASCADE
);

select * from Reports

-- ===============================
-- 5. Fact_Check Table
-- ===============================
CREATE TABLE Fact_Check (
    fact_id INT PRIMARY KEY AUTO_INCREMENT,
    article_id INT UNIQUE NOT NULL,
    verdict ENUM('True','False','Partially True') NOT NULL,
    checked_by INT NOT NULL,
    check_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (article_id) REFERENCES Articles(article_id) ON DELETE CASCADE,
    FOREIGN KEY (checked_by) REFERENCES Users(user_id)
);

select * from Fact_Check

-- ===============================
-- 6. Stored Procedure
-- ===============================
DELIMITER $$

CREATE PROCEDURE RecalculateTrustScore(IN art_id INT)
BEGIN
    DECLARE src_score DECIMAL(5,2) DEFAULT 50;
    DECLARE fact_score DECIMAL(5,2) DEFAULT 50;
    DECLARE report_count INT DEFAULT 0;
    DECLARE final_score DECIMAL(5,2);

    SELECT s.credibility_score INTO src_score
    FROM Articles a JOIN Sources s ON a.source_id=s.source_id
    WHERE a.article_id=art_id;

    SELECT CASE
        WHEN verdict='True' THEN 100
        WHEN verdict='Partially True' THEN 50
        WHEN verdict='False' THEN 0
        ELSE 50
    END INTO fact_score
    FROM Fact_Check WHERE article_id=art_id LIMIT 1;

    SELECT COUNT(*) INTO report_count FROM Reports WHERE article_id=art_id;

    SET final_score=(src_score*0.5)+(fact_score*0.3)-(report_count*2);

    IF final_score<0 THEN SET final_score=0;
    ELSEIF final_score>100 THEN SET final_score=100;
    END IF;

    UPDATE Articles SET trust_score=final_score WHERE article_id=art_id;
END $$

DELIMITER ;

-- ===============================
-- 7. Triggers
-- ===============================
DELIMITER $$

CREATE TRIGGER after_report_insert
AFTER INSERT ON Reports
FOR EACH ROW
BEGIN
    CALL RecalculateTrustScore(NEW.article_id);
END $$

CREATE TRIGGER after_factcheck_insert
AFTER INSERT ON Fact_Check
FOR EACH ROW
BEGIN
    CALL RecalculateTrustScore(NEW.article_id);
END $$

CREATE TRIGGER after_factcheck_update
AFTER UPDATE ON Fact_Check
FOR EACH ROW
BEGIN
    CALL RecalculateTrustScore(NEW.article_id);
END $$

DELIMITER ;
