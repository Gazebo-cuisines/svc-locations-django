-- Legacy unit / stock / BOM SQL extracted from data/Dump20260720.sql
-- Regenerate: python scripts/extract_legacy_unit_sql.py
-- See UNIT_CALC_LEGACY_SQL.md for Django porting notes.

USE production;

-- ========== tblunits DDL + seed ==========
DROP TABLE IF EXISTS `tblunits`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `tblunits` (
  `id` int NOT NULL AUTO_INCREMENT,
  `unit` varchar(16) DEFAULT NULL,
  `weightbased` tinyint DEFAULT NULL,
  `converttounit` int DEFAULT NULL,
  `convertmultiplier` decimal(10,4) DEFAULT NULL,
  `locked` tinyint DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `idxUnitNameUNQ` (`unit`)
) ENGINE=InnoDB AUTO_INCREMENT=8 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `tblunits`
--

LOCK TABLES `tblunits` WRITE;
/*!40000 ALTER TABLE `tblunits` DISABLE KEYS */;
INSERT INTO `tblunits` VALUES (1,'unit',0,1,1.0000,-1),(2,'grams',-1,5,0.0010,-1),(3,'meters',0,NULL,NULL,-1),(4,'seconds',0,NULL,NULL,-1),(5,'Kg',-1,2,1000.0000,0),(6,'Box',0,1,1.0000,0),(7,'Liter',0,2,1000.0000,0);
/*!40000 ALTER TABLE `tblunits` ENABLE KEYS */;
UNLOCK TABLES;

-- ========== tblunits triggers ==========
/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`%`*/ /*!50003 TRIGGER `tblunits_AFTER_INSERT` AFTER INSERT ON `tblunits` FOR EACH ROW BEGIN

    CALL `production`.`procSYSlogActivity`(
											NULL, NULL, NULL, NULL,
                                            "Units", 
                                            "tblUnits", 
                                            new.`id`, 
                                            "INSERT", 
                                            CONCAT(
													"INSERT UNIT: ", new.`unit`, " -- With ID: ", new.`id`
                                                    )
                                            );

END */;;
/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`%`*/ /*!50003 TRIGGER `tblunits_BEFORE_UPDATE` BEFORE UPDATE ON `tblunits` FOR EACH ROW BEGIN

	DECLARE rowCount INT;
	DECLARE sourceIPcount INT;
    
    SELECT count(`tsv`.`originIP`) INTO sourceIPcount FROM `production`.`tblAtERPStateVector` tsv
		WHERE `tsv`.`originIP` = SUBSTRING_INDEX(USER(), '@', -1)
        AND `tsv`.`source` = 'ITEMSUNITS' AND `tsv`.`action` = 'ACTIVECHECKLOCK';
    
	IF ( sourceIPcount = 0 ) THEN
    
		SET rowCount = (SELECT count(`id`) FROM `production`.`tblproducts` WHERE `tblproducts`.`unit` = old.id);

		IF rowCount > 0 THEN
        
			SIGNAL SQLSTATE '45000'
			SET MESSAGE_TEXT = 'Item Definitions - Selected Item Can Not be Changed. Has Items Under';
            
		END IF;
    
    END IF;
    
/*
	IF TRUE AND
		(
		new.`weightbased` <> old.`weightbased` OR
        new.`converttounit` <> old.`converttounit` OR
        new.`convertmultiplier` <> old.`convertmultiplier`
        )
    
    THEN
    
		SET rowCount = (SELECT count(id) FROM `production`.`tblproducts` WHERE `tblproducts`.`unit` = old.id);

		IF rowCount > 0 THEN
			SIGNAL SQLSTATE '45000'
			SET MESSAGE_TEXT = 'Item Definitions - Selected Item Can Not be Changed. Has Items Under';
		END IF;
    
    END IF;
*/

END */;;
/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`%`*/ /*!50003 TRIGGER `tblunits_BEFORE_DELETE` BEFORE DELETE ON `tblunits` FOR EACH ROW BEGIN

	DECLARE rowCount INT;
    
	SET rowCount = (SELECT count(`id`) FROM `production`.`tblproducts` WHERE `tblproducts`.`unit` = old.id);

	IF rowCount > 0 THEN
    
		SIGNAL SQLSTATE '45000'
		SET MESSAGE_TEXT = 'Item Definitions - Selected Item Can Not be Changed. Has Items Under';
        
	END IF;

END */;;
/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`%`*/ /*!50003 TRIGGER `tblunits_AFTER_DELETE` AFTER DELETE ON `tblunits` FOR EACH ROW BEGIN

    CALL `production`.`procSYSlogActivity`(
											NULL, NULL, NULL, NULL,
                                            "Units", 
                                            "tblUnits", 
                                            old.`id`, 
                                            "DELETE", 
                                            CONCAT(
													"DELETE UNIT: ", old.`unit`, " -- With ID: ", old.`id`
                                                    )
                                            );

END */;;

-- ========== functions ==========
-- source: DROP FUNCTION IF EXISTS `fnGetUnitConversionFactor`
CREATE DEFINER=`root`@`%` FUNCTION `fnGetUnitConversionFactor`(prmUnitFrom INT, prmUnitTo INT) RETURNS decimal(10,4)
    DETERMINISTIC
BEGIN

	DECLARE result DECIMAL(10, 4);
    
	SELECT tun.`convertmultiplier` INTO result FROM `production`.`tblunits` tun WHERE tun.`id` = prmUnitFrom and tun.`converttounit` = prmUnitTo;
    
    IF prmUnitFrom = prmUnitTo THEN SET result = 1; END IF;
    
    RETURN coalesce(result, 1);
    
END ;;

-- source: DROP FUNCTION IF EXISTS `fnGetUnitForItem`
CREATE DEFINER=`root`@`%` FUNCTION `fnGetUnitForItem`(`itemid` INT) RETURNS int
    DETERMINISTIC
BEGIN

	DECLARE result INT;
    
	SELECT unit INTO result FROM tblproducts WHERE id = itemid;
    
    RETURN result;
    
END ;;

-- source: DROP FUNCTION IF EXISTS `fnGetUnitName`
CREATE DEFINER=`root`@`%` FUNCTION `fnGetUnitName`(`unitid` INT) RETURNS varchar(16) CHARSET utf8mb4
    DETERMINISTIC
BEGIN

	DECLARE result VARCHAR(16);
    
	SELECT unit INTO result FROM tblunits WHERE id = unitid;
    
    RETURN result;
    
END ;;

-- source: DROP FUNCTION IF EXISTS `fnGetUnitNameForItem`
CREATE DEFINER=`root`@`%` FUNCTION `fnGetUnitNameForItem`(`itemid` INT) RETURNS varchar(16) CHARSET utf8mb4
    DETERMINISTIC
BEGIN
	
    DECLARE result VARCHAR(16);
    
    SELECT unit INTO result FROM tblunits WHERE id = `production`.`fnGetUnitForItem`(itemid);
    
    RETURN result;

END ;;

-- source: DROP FUNCTION IF EXISTS `fnGetBatchSizeFinalForItem`
CREATE DEFINER=`root`@`%` FUNCTION `fnGetBatchSizeFinalForItem`(`itemid` INT) RETURNS decimal(16,6)
    DETERMINISTIC
BEGIN

	DECLARE result DECIMAL(16, 6);
    
    SELECT tblproducts.`unitaryweight` INTO result FROM `production`.`tblproducts` WHERE tblproducts.`id` = itemid;
    
    RETURN result;
    
END ;;

-- source: DROP FUNCTION IF EXISTS `fnGetBatchSizeRawForItem`
CREATE DEFINER=`root`@`%` FUNCTION `fnGetBatchSizeRawForItem`(`itemid` INT) RETURNS decimal(16,6)
    DETERMINISTIC
BEGIN

	DECLARE result DECIMAL(16, 6);
    
    SELECT tblproducts.`grossunitaryweight` INTO result FROM `production`.`tblproducts` WHERE tblproducts.`id` = itemid;
    
    RETURN result;
    
END ;;

-- source: DROP FUNCTION IF EXISTS `fnSTKgetItemProcessLoss`
CREATE DEFINER=`root`@`%` FUNCTION `fnSTKgetItemProcessLoss`(prmItem INT) RETURNS decimal(10,4)
    DETERMINISTIC
mainLoop:BEGIN

	DECLARE result DECIMAL(10, 4);
    
	-- IF RECIPE HAS MIXED UNITS --> RETURN 1
	IF (SELECT count(DISTINCT tpt.`unit`) FROM `production`.`tblproducttree` tpt WHERE tpt.`parentprod` = prmItem) > 1 THEN
		RETURN 1;
        LEAVE mainLoop;
    
    END IF; 
    
    SELECT tpv.`processLoss` INTO result FROM `production`.`tblnpdproducttreeversion` tpv WHERE tpv.`active` = -1 AND tpv.`item` = prmItem;
    RETURN coalesce(result, 1);

/*
	-- RETURN AVERAGE YIELD FOR RECIPE - NOT WEIGHED AVERGE YIELD
    -- `quantity` ALWAYS EXISTS
	SELECT (sum(tpt.`quantity` * tpt.`productyield`)) / sum(tpt.`quantity`) INTO result FROM `production`.`tblproducttree` tpt
    WHERE TRUE 
    AND tpt.`parentprod` = prmItem;
    
	RETURN coalesce(result, 1);
*/

END ;;

-- source: DROP FUNCTION IF EXISTS `fnSTKgetItemYield`
CREATE DEFINER=`root`@`%` FUNCTION `fnSTKgetItemYield`(prmitem INT) RETURNS decimal(10,4)
    DETERMINISTIC
BEGIN

	DECLARE result DECIMAL(10, 4);
    
	SELECT productyield INTO result FROM tblproducts WHERE id = prmitem;
    
    RETURN result;
    
END ;;

-- source: DROP FUNCTION IF EXISTS `fnSTKtransactionMultiplier`
CREATE DEFINER=`root`@`%` FUNCTION `fnSTKtransactionMultiplier`(prmitem INT, prmshapeformat INT, prmfrom INT, prmto INT) RETURNS decimal(16,6)
    DETERMINISTIC
fnflow:
BEGIN
    
    DECLARE shapeID INT;
    DECLARE shapeMultiplier DECIMAL(16,6);
    DECLARE unitFromToMultiplier INT;
    
    DECLARE fromInternal BOOLEAN;
    DECLARE fromStorage BOOLEAN;
    DECLARE toInternal BOOLEAN;
    DECLARE toStorage BOOLEAN;
    
	SET fromInternal = ( SELECT abs(`internal`) FROM `production`.tblContainers WHERE id = prmfrom ) = 1;
    SET fromStorage = ( SELECT abs(`storage`) FROM `production`.tblContainers WHERE id = prmfrom ) = 1;
    SET toInternal = ( SELECT abs(`internal`) FROM `production`.tblContainers WHERE id = prmto ) = 1;
    SET toStorage = ( SELECT abs(`storage`) FROM `production`.tblContainers WHERE id = prmto ) = 1;
    
    IF isnull(prmshapeformat) THEN
		RETURN 1;
        LEAVE fnflow;
	END IF;
    
    IF TRUE
		-- AND ( SELECT `storage` FROM `production`.`tblContainers` WHERE id = prmfrom ) = ( SELECT `storage` FROM `production`.`tblContainers` WHERE id = prmto )
		-- AND ( SELECT `internal` FROM `production`.`tblContainers` WHERE id = prmfrom ) = ( SELECT `internal` FROM `production`.`tblContainers` WHERE id = prmto )
        AND fromInternal = toInternal
        AND fromStorage = toStorage
    THEN
		RETURN 1;
        LEAVE fnflow;
	END IF;
    
    IF TRUE
        -- AND ((( SELECT `storage` FROM `production`.`tblContainers` WHERE id = prmfrom ) = -1) AND (( SELECT `storage` FROM `production`.`tblContainers` WHERE id = prmto ) = 0))
        AND (fromStorage AND NOT toStorage)
        OR
			(	TRUE
				-- AND ((( SELECT `internal` FROM `production`.`tblContainers` WHERE id = prmfrom ) = 0) AND (( SELECT `internal` FROM `production`.`tblContainers` WHERE id = prmto ) = -1))
                -- AND (( SELECT `storage` FROM `production`.`tblContainers` WHERE id = prmto ) = 0)
                AND (NOT fromInternal AND toInternal)
                AND (NOT toStorage)
            )
	THEN
		SELECT id, multiplier INTO shapeID, shapeMultiplier FROM `production`.tblProductsMappingSupplier WHERE id = prmshapeformat;
        SET unitFromToMultiplier = `production`.`fnGetUnitConversionFactor`	(
																				( 
																						SELECT purchasingunit 
																						FROM `production`.`tblproducts`
																						WHERE id = prmitem 
																				), 
																				(
																					SELECT unit
																					FROM `production`.`tblproducts`
                                                                                    WHERE id = prmitem 
																				)
																			);
		RETURN shapeMultiplier * unitFromToMultiplier;
        LEAVE fnflow;
	END IF;
    
    RETURN 1;
    
END ;;

-- source: DROP FUNCTION IF EXISTS `fnSTKtransactionShapeFormatForStockTuple`
CREATE DEFINER=`root`@`%` FUNCTION `fnSTKtransactionShapeFormatForStockTuple`(prmItem INT, prmShapeFormat INT) RETURNS decimal(16,6)
    DETERMINISTIC
fnflow:
BEGIN
    
    DECLARE itemNaturalUnit INT;
    DECLARE itemPurchaseUnit INT;
    DECLARE unitConversionFactor DECIMAL(10, 4);
    DECLARE shapeMultiplier DECIMAL(16, 6);
    
    DECLARE unitFromToMultiplier INT;
    
    IF isnull(prmItem) or isnull(prmShapeFormat) THEN
		RETURN null;
        LEAVE fnflow;
	END IF;
    
    IF ( select `storage` from `production`.tblContainers tcn where tcn.`id` = prmShapeFormat ) = 0 THEN
		RETURN 1;
        LEAVE fnflow;
	END IF;
    
    SET itemNaturalUnit = ( SELECT tpd.`unit` FROM `production`.`tblproducts` tpd WHERE tpd.`id` = prmItem );
    SET itemPurchaseUnit = ( SELECT tpd.`purchasingunit` FROM `production`.`tblproducts`tpd  WHERE tpd.`id` = prmItem );
    SET unitConversionFactor = `production`.`fnGetUnitConversionFactor`(itemPurchaseUnit, itemNaturalUnit);
    SET shapeMultiplier = (`production`.tblProductsMappingSupplier);
    
    RETURN unitConversionFactor;
    
    /*
	SELECT tpmsf.`id`, tpmsf.`multiplier` INTO shapeID, shapeMultiplier FROM `production`.`tblProductsMappingSupplier` tpmsf where tpmsf.`id` = prmshapeformat;
	SET unitFromToMultiplier = production.fnGetUnitConversionFactor( ( select purchasingunit from tblproducts where id = prmitem ), ( select unit from tblproducts where id = prmitem ));
	RETURN shapeMultiplier * unitFromToMultiplier;
	LEAVE fnflow;
    */
    
END ;;

-- source: DROP FUNCTION IF EXISTS `fnSTKtransactionShapeFormatUnitSize`
CREATE DEFINER=`root`@`%` FUNCTION `fnSTKtransactionShapeFormatUnitSize`(prmitem INT, prmshape INT) RETURNS decimal(16,6)
    DETERMINISTIC
BEGIN

	DECLARE result DECIMAL(16, 6);
    
	SELECT coalesce((SELECT multiplier FROM production.tblProductsMappingSupplier where id = prmshape)
		  * `production`.`fnGetUnitConversionFactor`(( select purchasingunit from tblproducts where id = prmitem ), ( select unit from tblproducts where id = prmitem )), 1)
          INTO result;
                
	RETURN result;
    
END ;;

-- ========== procedures ==========
-- source: DROP PROCEDURE IF EXISTS `procSTKstockIN`
CREATE DEFINER=`root`@`%` PROCEDURE `procSTKstockIN`(
														IN prmUserID INT,
                                                        IN prmLanUserName VARCHAR(32),
                                                        IN prmSrcWorkStation VARCHAR(16),
                                                        IN prmPOID INT,
                                                        IN prmSalesOrderID INT,
														IN prmBatchID INT,
                                                        IN prmItem INT,
                                                        IN prmUnit INT,
                                                        IN prmDate DATE,
                                                        IN prmPONumber VARCHAR(16),
														IN prmProdDate DATE,
                                                        IN prmUseBy DATE,
                                                        IN prmTNbr INT,
                                                        IN prmSrcCnt INT,
                                                        IN prmDestCnt INT,
														IN prmStkIn DECIMAL(16,6),
                                                        IN prmReceipeVersion INT,
                                                        IN prmShapeFormat INT
													)
BEGIN

	DECLARE stockINquantity DECIMAL(16, 6);

	-- ######  SETUP ENTRY QUANTITY
	-- ######  TEST IF DESTINATION CONTAINER IS STORAGE ==> STOCK STORED AS SHAPE FORMAT
	IF TRUE
		AND (( select tcn.`storage` FROM `production`.tblContainers tcn WHERE tcn.`id` = prmDestCnt ) = 0 ) 
	THEN
    
        SET prmStkIn = prmStkIn * `production`.`fnSTKtransactionMultiplier`(prmItem, prmShapeFormat, prmSrcCnt, prmDestCnt);
        
	END IF;
	-- ######  TEST IF DESTINATION CONTAINER IS STORAGE ==> STOCK STORED AS SHAPE FORMAT
    
	INSERT INTO `production`.`tblstockmovement`
	(
		`user`, `lanusername`, `srcworkstation`, `srcworkstationip`, `poID`, `salesOrderID`,
		`stkbatchidtrail`, `action`, `item`, `productname`, `productreceipecode`,
		`unit`, `unitkey`, `date`, `ponumber`, `productiondate`, `useby`, `tracenumber`, `range`,
		`srccontainer`, `srccontainern`, `destcontainer`, `destcontainern`, `stkin`, `productreceipecodeversion`, `shapeformat`
	)
	SELECT
		prmUserID, prmLanUserName, prmSrcWorkStation, `production`.`fnGetConnectionIP`(), prmPOID, prmSalesOrderID,
		prmBatchID, 'STOCKIN', prmItem, `tpd`.`productname`, `tpd`.`productreceipecode`,
		`tun`.`unit`, prmUnit, prmDate, prmPONumber, prmProdDate, prmUseBy, prmTNbr, `trg`.`id`,
		-- prmSrcCnt, `tcns`.`container`, prmDestCnt, `tcnd`.`container`, prmStkIn, prmReceipeVersion, prmShapeFormat
        prmSrcCnt, `tcns`.`container`, prmDestCnt, `tcnd`.`container`, prmStkIn, prmReceipeVersion, prmShapeFormat
	FROM `production`.`tblproducts` `tpd`
        INNER JOIN `production`.`tblunits` `tun` ON `tun`.`id` = prmUnit
        INNER JOIN `production`.tblContainers `tcns` ON `tcns`.`id` = prmSrcCnt
        INNER JOIN `production`.tblContainers `tcnd` ON `tcnd`.`id` = prmDestCnt
        INNER JOIN `production`.`tblrange` `trg` ON `trg`.`id` = `tpd`.`range`
	WHERE `tpd`.`id` = prmItem;

END ;;

-- source: DROP PROCEDURE IF EXISTS `procSTKstockINprocess`
CREATE DEFINER=`root`@`%` PROCEDURE `procSTKstockINprocess`(
																IN prmaction VARCHAR(32),
                                                                IN prmlastingap INT,
                                                                IN prmitem INT,
                                                                IN prmresource INT,
																IN prmshift INT,
                                                                IN prmstaff INT,
                                                                IN prmstart TIME,
                                                                IN prmend TIME,
                                                                IN prmhits decimal(4,2),
																IN prmrcpcodeversion INT,
                                                                IN prmunit INT,
																IN prmdate DATE,
                                                                IN prmponumber VARCHAR(16),
																IN prmproddate DATE,
                                                                IN prmuseby DATE,
                                                                IN prmtnbr INT,
                                                                IN prmsrccnt INT,
                                                                IN prmdestcnt INT,
																IN prmstkin DECIMAL(16,6)
															)
BEGIN

	INSERT INTO tblstockmovement
	(
		`action`, lastingap, item, `resource`, productname, productreceipecode,
		unit, unitkey, `date`, `ponumber`, productiondate, useby, tracenumber, `range`,
		shift, staff, `start`, `end`, lrccp1nofbatches,
		srccontainer, srccontainern, destcontainer, destcontainern, stkin, productreceipecodeversion
    )
	VALUES 
	(
		prmaction, prmlastingap, prmitem, prmresource, `production`.`fnGetProductName`(prmitem), `production`.`fnGetProductReceipeCode`(prmitem),
		`production`.`fnGetUnitName`(prmunit), prmunit, prmdate, prmponumber, prmproddate, prmuseby, prmtnbr, `production`.`fnGetProductRange`(prmitem),
		prmshift, prmstaff, prmstart, prmend, prmhits,
		prmsrccnt, `production`.`fnGetContainerName`(prmsrccnt), prmdestcnt,`production`.`fnGetContainerName`(prmdestcnt), prmstkin, prmrcpcodeversion
    );
    
END ;;

-- source: DROP PROCEDURE IF EXISTS `procSTKstockOUT`
CREATE DEFINER=`root`@`%` PROCEDURE `procSTKstockOUT`(
														IN prmUserID INT,
                                                        IN prmLanUserName VARCHAR(32),
                                                        IN prmSrcWorkStation VARCHAR(16),
                                                        IN prmpoID INT,
                                                        IN prmSalesOrderID INT,
														IN prmBatchID INT,
                                                        IN prmItem INT,
                                                        IN prmUnit INT,
                                                        IN prmDate DATE,
                                                        IN prmPONumber VARCHAR(16),
														IN prmProdDate DATE,
                                                        IN prmUseBy DATE,
                                                        IN prmTNbr INT,
                                                        IN prmSrcCnt INT,
                                                        IN prmDestCnt INT,
														IN prmStkOut DECIMAL(16,6),
                                                        IN prmStkOutNet DECIMAL(16,6),
                                                        IN prmReceipeVersion INT,
                                                        IN prmShapeFormat INT
													)
BEGIN

	INSERT INTO `production`.`tblstockmovement`
	(
		`user`, `lanusername`, `srcworkstation`, `srcworkstationip`, `poID`, `salesOrderID`,
		`stkbatchidtrail`, `action`, `item`, `productname`, `productreceipecode`,
		`unit`, `unitkey`, `date`, `ponumber`, `productiondate`, `useby`, `tracenumber`, `range`,
		`srccontainer`, `srccontainern`, `destcontainer`, `destcontainern`, `stkout`, `productreceipecodeversion`, `shapeformat`
    )
	SELECT
		prmUserID, prmLanUserName, prmSrcWorkStation, `production`.`fnGetConnectionIP`(), prmpoID, prmSalesOrderID,
		prmBatchID, 'STOCKOUT', prmItem, `tpd`.`productname`, `tpd`.`productreceipecode`,
		`tun`.`unit`, prmUnit, prmDate, prmPONumber, prmProdDate, prmUseBy, prmTNbr, `trg`.`id`,
		prmSrcCnt, `tcns`.`container`, prmDestCnt, `tcnd`.`container`, prmStkOut, prmReceipeVersion, prmShapeFormat
	FROM `production`.`tblproducts` `tpd`
	INNER JOIN `production`.`tblunits` `tun` ON `tun`.`id` = prmUnit
	INNER JOIN `production`.`tblContainers` `tcns` ON `tcns`.`id` = prmSrcCnt
	INNER JOIN `production`.`tblContainers` `tcnd` ON `tcnd`.`id` = prmDestCnt
	INNER JOIN `production`.`tblrange` `trg` ON `trg`.`id` = `tpd`.`range`
	WHERE `tpd`.`id` = prmItem;

END ;;

-- source: DROP PROCEDURE IF EXISTS `procSTKstockRECON`
CREATE DEFINER=`root`@`%` PROCEDURE `procSTKstockRECON`(
															IN prmUserID INT,
                                                            IN prmLanUserName VARCHAR(32),
                                                            IN prmSrcWorkStation VARCHAR(16),
															IN prmBatchID INT,
                                                            IN prmItem INT,
                                                            IN prmUnit INT,
                                                            IN prmDate DATE,
                                                            IN prmPONumber VARCHAR(16),
															IN prmProdDate DATE,
                                                            IN prmUseBy DATE,
                                                            IN prmTNbr INT,
                                                            IN prmSrcCnt INT,
                                                            IN prmDestCnt INT,
															IN prmSTKRecon DECIMAL(16,6),
                                                            IN prmRealStock DECIMAL(16,6),
                                                            IN prmReceipeVersion INT,
                                                            IN prmShapeFormat INT
														)
BEGIN

	INSERT INTO `production`.`tblstockmovement`
	(
		`user`, `lanusername`, `srcworkstation`, `srcworkstationip`,
		`stkbatchidtrail`, `action`, `item`, `productname`, `productreceipecode`,
		`unit`, `unitkey`, `date`, `ponumber`, `productiondate`, `useby`, `tracenumber`, `range`,
		`srccontainer`, `srccontainern`, `destcontainer`, `destcontainern`, `stkrecon`, `stkAtRecon`, `productreceipecodeversion`, `shapeformat`
	)
	SELECT
		prmUserID, prmLanUserName, prmSrcWorkStation, `production`.`fnGetConnectionIP`(),
		prmBatchID, 'STOCKRECON', prmItem, `tpd`.`productname`, `tpd`.`productreceipecode`,
		`tun`.`unit`, prmUnit, prmDate, prmPONumber, prmProdDate, prmUseBy, prmTNbr, `trg`.`id`,
		prmSrcCnt, `tcns`.`container`, prmDestCnt, `tcnd`.`container`, prmSTKRecon, 
		`production`.`fnSTKitemStockAllAttributes`(
														1, 0, 'OVBSTK', 'ORLSTK', 'OITSTK',
														curdate(),
														prmItem,
														prmDestCnt,
														prmProdDate,
														prmUseBy,
														prmTNbr,
														prmReceipeVersion,
														prmShapeFormat,
														True,
														True
												   ),
        prmReceipeVersion, prmShapeFormat
	FROM `production`.`tblproducts` `tpd`
	INNER JOIN `production`.`tblunits` `tun` ON `tun`.`id` = prmUnit
	INNER JOIN `production`.`tblContainers` `tcns` ON `tcns`.`id` = prmSrcCnt
	INNER JOIN `production`.`tblContainers` `tcnd` ON `tcnd`.`id` = prmDestCnt
	INNER JOIN `production`.`tblrange` `trg` ON `trg`.`id` = `tpd`.`range`
	WHERE `tpd`.`id` = prmItem;

END ;;

-- source: DROP PROCEDURE IF EXISTS `procSTKstockTRANSFER`
CREATE DEFINER=`root`@`%` PROCEDURE `procSTKstockTRANSFER`(
															IN prmUserID INT,
															IN prmLanUserName VARCHAR(32),
															IN prmSrcWorkStation VARCHAR(16),
															IN prmpoID INT,
															IN prmSalesOrderID INT,
															IN prmBatchID INT,
															IN prmPickingListJob INT,
															IN prmItem INT,
															IN prmUnit INT,
															IN prmDate DATE,
															IN prmPOnumber VARCHAR(16),
															IN prmProdDate DATE,
															IN prmUseBy DATE, 
															IN prmTNbr INT, 
															IN prmSrcCnt INT, 
															IN prmDestCnt INT,
															IN prmStkTrf DECIMAL(16,6), 
															IN prmReceipeVersion INT, 
															IN prmShapeFormat INT,
															IN prmNewUseby DATE, 
															IN prmUseByModifier INT, 
															IN prmExternalTransactionID INT,
                                                            IN prmAction VARCHAR(32)
														)
BEGIN

	DECLARE intnextSTOCKTRANSFERid INT;
    DECLARE TransactionOUTshapeFormat INT;
    
    SET TransactionOUTshapeFormat = prmshapeformat;
    
    -- SELECT COALESCE( MAX(`stktransfertrail`), 0) + 1 INTO intnextSTOCKTRANSFERid FROM tblstockmovement WHERE `action`='STOCKTRANSFER';
    SET intnextSTOCKTRANSFERid = `production`.`fnGenerateNextTrailCounter`('STOCKTRANSFER', 'tblstockmovement', 'stktransfertrail');

	INSERT INTO `production`.`tblstockmovement`
	(
		`user`, `lanusername`, `srcworkstation`, `srcworkstationip`, `poID`, `salesOrderID`,
		`stkbatchidtrail`, `stktransfertrail`, `action`, `item`, `productname`, `productreceipecode`,
		`unit`, `unitkey`, `date`, `ponumber`, `productiondate`, `useby`, `tracenumber`, `range`,
		`srccontainer`, `srccontainern`, `destcontainer`, `destcontainern`, `stkout`, `stkoutnet`, `productreceipecodeversion`, `shapeformat`, `externalTransactionID`
    )
    SELECT
		`prmuserid`, `prmlanusername`, `prmsrcworkstation`, `production`.`fnGetConnectionIP`(), `prmpoID`, `prmsalesOrderID`,
		`prmbatchid`, intnextSTOCKTRANSFERid, 
		CASE
			WHEN NOT ISNULL(`prmsalesOrderID`) THEN 'SALESORDER'
            WHEN NOT ISNULL(prmAction) THEN prmAction
            ELSE 'STOCKTRANSFER'
		END,
        `prmitem`, `tpd`.`productname`, `tpd`.`productreceipecode`,
		`tun`.`unit`, `prmunit`, `prmdate`, `prmponumber`, `prmproddate`, `prmuseby`, `prmtnbr`, `trg`.`id`,
		prmdestcnt, `tcnd`.`container`, prmsrccnt, `tcns`.`container`, 
        `prmstktrf`, null, `prmreceipeversion`, `prmshapeformat`, `prmexternalTransactionID`
	FROM `production`.`tblproducts` `tpd`
	INNER JOIN `production`.`tblunits` `tun` ON `tun`.`id` = prmunit
	INNER JOIN `production`.`tblContainers` `tcns` ON `tcns`.`id` = prmsrccnt
	INNER JOIN `production`.`tblContainers` `tcnd` ON `tcnd`.`id` = prmdestcnt
	INNER JOIN `production`.`tblrange` `trg` ON `trg`.`id` = `tpd`.`range`
	WHERE `tpd`.`id` = prmitem;

	IF TRUE
		AND (( select tcn.`storage` FROM `production`.`tblContainers` tcn WHERE tcn.`id` = prmsrccnt ) = -1 ) 
		AND (( select tcn.`storage` FROM `production`.`tblContainers` tcn WHERE tcn.`id` = prmdestcnt ) = 0 ) 
	THEN
		SET prmshapeformat = NULL;
	END IF;
	IF prmnewuseby <> prmuseby THEN SET prmuseby = prmnewuseby; END IF;
    
	-- DETECTS A STOCK TRANSACTION - FULFILL CUSTOMER SALES ORDERS - CHECK BALANCE QUANTITY AND INSERT IN ANOMALIES IF BALANCE <> 0
    
    IF
		(
			SELECT
				count(tsm.`id`)
			FROM
				`production`.`tblstockmovement` tsm
			INNER JOIN `production`.`tblContainers` ON tsm.`destcontainer` = `tblContainers`.`id` AND `tblContainers`.`customer` = -1
																								AND NOT isnull(tsm.`salesOrderID`)
                                                                                                AND NOT isnull(tsm.`stkin`)
			INNER JOIN `production`.`tblproducts` ON tsm.`item` = `tblproducts`.`id` AND `tblproducts`.`genIsDispatchSupport` = 0
			WHERE TRUE
				AND tsm.`livetransaction` = -1
				AND NOT isnull(tsm.`externalTransactionID`) 
				AND tsm.`externalTransactionID` = prmexternalTransactionID
		) > 0
	THEN
		SELECT tpd.`quantityBalance` INTO @qtyBalance FROM `production`.`tblpoordersordersdetails` tpd WHERE tpd.`transactionid` = prmexternalTransactionID;
        
		IF @qtyBalance = 0 THEN SELECT 'Complete and Matching'; END IF;
		IF @qtyBalance < 0 THEN SELECT 'Over supplied'; END IF;
		IF @qtyBalance > 0 THEN SELECT 'Under Supplied'; END IF;
	END IF;

	INSERT INTO `production`.`tblstockmovement`
	(
		`user`, `lanusername`, `srcworkstation`, `srcworkstationip`, `poID`, `salesOrderID`,
		`stkbatchidtrail`, `stktransfertrail`, `action`, `item`, `productname`, `productreceipecode`,
		`unit`, `unitkey`, `date`, `ponumber`, `productiondate`, `useby`, `tracenumber`, `range`,
		`srccontainer`, `srccontainern`, `destcontainer`, `destcontainern`, `stkin`, `productreceipecodeversion`, `shapeformat`, `externalTransactionID`
    )
    SELECT
		prmuserid, prmlanusername, prmsrcworkstation, `production`.`fnGetConnectionIP`(), `prmpoID`, `prmsalesOrderID`, 
		`prmbatchid`, intnextSTOCKTRANSFERid,
		CASE
			WHEN NOT ISNULL(`prmsalesOrderID`) THEN 'SALESORDER'
            WHEN NOT ISNULL(prmAction) THEN prmAction
			ELSE 'STOCKTRANSFER'
		END,
        `prmitem`, `tpd`.`productname`, `tpd`.`productreceipecode`,
		`tun`.`unit`, `prmunit`, `prmdate`, `prmponumber`, `prmproddate`, `prmuseby`, `prmtnbr`, `trg`.`id`,
		prmsrccnt, `tcns`.`container`, prmdestcnt, `tcnd`.`container`,
        prmstktrf * `production`.`fnSTKtransactionMultiplier`(prmitem, TransactionOUTshapeFormat, prmsrccnt, prmdestcnt),
        prmreceipeversion, prmshapeformat, prmexternalTransactionID
	FROM `production`.`tblproducts` `tpd`
	INNER JOIN `production`.`tblunits` `tun` ON `tun`.`id` = prmunit
	INNER JOIN `production`.`tblContainers` `tcns` ON `tcns`.`id` = prmsrccnt
	INNER JOIN `production`.`tblContainers` `tcnd` ON `tcnd`.`id` = prmdestcnt
	INNER JOIN `production`.`tblrange` `trg` ON `trg`.`id` = `tpd`.`range`
	WHERE `tpd`.`id` = prmitem;
    
	SET @qtyBalance = null;

END ;;

-- source: DROP PROCEDURE IF EXISTS `procSTKstockITEMTRANSFER`
CREATE DEFINER=`root`@`%` PROCEDURE `procSTKstockITEMTRANSFER`(
																	IN prmitem INTEGER,
                                                                    IN prmrcpcodeversion INT,
                                                                    IN prmunit INT,
                                                                    IN prmdate DATE,
                                                                    IN prmponumber VARCHAR(16),
																	IN prmproddate DATE,
                                                                    IN prmuseby DATE,
                                                                    IN prmtnbr INT,
                                                                    IN prmsrccnt INT,
                                                                    IN prmdestcnt INT,
																	IN prmstktrf DECIMAL(16,6),
                                                                    IN prmreceipeversion INT
																)
BEGIN

	DECLARE intnextSTOCKTRANSFERid INT;
    
    -- SELECT COALESCE(MAX(`stktransfertrail`), 0) + 1 INTO intnextSTOCKTRANSFERid FROM tblstockmovement WHERE `action`='STOCKTRANSFER';
	SET intnextSTOCKTRANSFERid = `production`.`fnGenerateNextTrailCounter`('STOCKTRANSFER', 'tblstockmovement', 'stktransfertrail');
    
	INSERT INTO tblstockmovement
	(
		`stktransfertrail`, `action`,item,productname,productreceipecode,
		unit,unitkey,`date`,`ponumber`,productiondate,useby,tracenumber, `range`,
		srccontainer,srccontainern,destcontainer,destcontainern,stkout,productreceipecodeversion
    )
	VALUES 
	(
		intnextSTOCKTRANSFERid, 'STOCKTRANSFER', prmitem, `production`.`fnGetProductName`(prmitem), `production`.`fnGetProductReceipeCode`(prmitem),
		`production`.`fnGetUnitName`(prmunit), prmunit, prmdate, prmponumber, prmproddate, prmuseby, prmtnbr, `production`.`fnGetProductRange`(prmitem),
		prmdestcnt,`production`.`fnGetContainerName`(prmdestcnt), prmsrccnt, `production`.`fnGetContainerName`(prmsrccnt), prmstktrf, prmreceipeversion
	);
    
    -- ADDED ON 18 MAY FOR COMMIT STOCKOUT TRANSACTION
    -- COMMIT;    
    
	INSERT INTO tblstockmovement
	(
		`stktransfertrail`, `action`, item, productname, productreceipecode,
		unit, unitkey, `date`, `ponumber`, productiondate, useby, tracenumber, `range`,
		srccontainer,srccontainern,destcontainer,destcontainern,stkin,productreceipecodeversion
	)
	VALUES 
	(
		intnextSTOCKTRANSFERid, 'STOCKTRANSFER', prmitem, `production`.`fnGetProductName`(prmitem), `production`.`fnGetProductReceipeCode`(prmitem),
		`production`.`fnGetUnitName`(prmunit), prmunit, prmdate, prmponumber, prmproddate, prmuseby, prmtnbr, `production`.`fnGetProductRange`(prmitem),
		prmsrccnt, `production`.`fnGetContainerName`(prmsrccnt), prmdestcnt,`production`.`fnGetContainerName`(prmdestcnt), prmstktrf, prmreceipeversion
	);

END ;;

-- source: DROP PROCEDURE IF EXISTS `procSTKitemGlobalAbsoluteStockDetails`
CREATE DEFINER=`root`@`%` PROCEDURE `procSTKitemGlobalAbsoluteStockDetails`(IN prmitem INT)
BEGIN

	SELECT
		tblstockcache.id,
		tblstockcache.item,
		tblstockcache.itemname,
		tblstockcache.itemreceipecode,
		tblstockcache.productreceipecodeversion,
		tblstockcache.shapeformat,
		tblstockcache.quantity,
		tblstockcache.unit,
		tblstockcache.destcontainer,
		tblstockcache.destcontainername,
		tblstockcache.date,
		tblstockcache.productiondate,
		tblstockcache.useby,
		tblstockcache.tracenumber,
		tblProductsMappingSupplier.shapeformat,
		tblProductsMappingSupplier.multiplier,
        tblunits_1.id AS unitfrom,
		tblunits.id AS unitto,
		production.fnGetUnitConversionFactor(tblunits_1.id, tblunits.id) AS unitconversion,
		tblstockcache.quantity * tblProductsMappingSupplier.multiplier * production.fnGetUnitConversionFactor(tblunits_1.id, tblunits.id) AS stock,
		CASE
		WHEN ISNULL(tblstockcache.quantity * tblProductsMappingSupplier.multiplier * production.fnGetUnitConversionFactor(tblunits_1.id, tblunits.id))
		THEN tblstockcache.quantity
		ELSE tblstockcache.quantity * tblProductsMappingSupplier.multiplier * production.fnGetUnitConversionFactor(tblunits_1.id, tblunits.id) END
		AS absolutestock,
		tblstockcache.unit AS itemunit
	FROM tblstockcache
		LEFT OUTER JOIN tblProductsMappingSupplier
		ON tblstockcache.shapeformat = tblProductsMappingSupplier.id
		LEFT OUTER JOIN tblunits
		ON tblstockcache.unit = tblunits.unit
		LEFT OUTER JOIN tblproducts
		ON tblstockcache.item = tblproducts.id
		LEFT OUTER JOIN tblunits tblunits_1
		ON tblproducts.purchasingunit = tblunits_1.id
	WHERE tblstockcache.item = prmitem;

END ;;

-- source: DROP PROCEDURE IF EXISTS `BOMrecursive000AtLevelExpand`
CREATE DEFINER=`root`@`%` PROCEDURE `BOMrecursive000AtLevelExpand`(
																		IN prmPlanID INT, 
																		IN prmStartLevel INT,
																		IN prmExternal BOOLEAN,
																		IN prmUser INT, 
																		IN prmLanUserName VARCHAR(32), 
																		IN prmSrcWorkStation VARCHAR(16), 
																		IN prmSrcWorkStationIP VARCHAR(16)
																	)
BEGIN

	SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;
    
	-- REBUILD LEVELS BELOW - SPLIT BY BATCHES

	SET sql_safe_updates = 0;
	DELETE FROM `production`.`tblBOMaggregateMEMORY` WHERE `id` > 0 AND `planmasterid` = prmPlanID AND `lvl` > prmStartLevel;
	SET sql_safe_updates = 1;

	INSERT INTO `production`.`tblBOMaggregateMEMORY`
		(
		 `user`, `lanusername`, `srcworkstation`, `srcworkstationip`,
		 `planmasterid`, `lvl`, `batchnumber`, `childitem`, `childitemreceipeversion`, `childitemname`,
         `childitemyield`, `minshelflife`, `planquantity`, 
		 `netrequired`, `grossrequired`, `processloss`, `srccontainer`, `destcontainer`,
         `stockatdestcontainer`,
         `overrideconsiderstock`, `overridebatchsplit`, `overridelastbatchfull`, `overrideMinStock`, `overrideMaxStock`,
         `remarks`
		)
	SELECT
		prmUser, prmLanUserName, prmSrcWorkStation, prmSrcWorkStationIP,
		prmplanid, oblout.`lvl`, oblout.`batchnumber`, oblout.`childitem`, `fnGetProductReceipeCodeVersion`(oblout.`childitem`), `fnGetProductName`(oblout.`childitem`),
		oblout.`productyield`, oblout.`minshelflife`, sum(oblout.`planquantity`) as `planquantity`,  
		sum(oblout.`netrequired`), sum(oblout.`grossrequired`),	avg(oblout.`processloss`), oblout.`childsource`, oblout.`itemsource`,
		`production`.`fnSTKitemStockAllAttributes`(
														1, 1, 'OVBSTK', 'ORLSTK', 'OITSTK',
														curdate(),
														oblout.`childitem`,
														`production`.`fnGetDestContainerIDForItem`(oblout.`childitem`),
														NULL,
														NULL,
														NULL,
														NULL,
														NULL,
														True,
														True
												   ),
		tpd.`genconsiderstockinplan`, tpd.`genfullbatches`, tpd.`unitaryweightalign`, tpd.`minstock`, tpd.`maxstock`,
        NULL
	FROM
		(
		SELECT
		prmplanid, obl.`lvl`, obl.`batchnumber`, obl.`childitem`, `fnGetProductName`(obl.`childitem`), obl.`productyield`, sum(obl.`planquantity`) as `planquantity`,
		obl.`minshelflife`,
		obl.`netrequired`, obl.`grossrequired`,	`fnGetSrcContainerIDForItem`(obl.`childitem`) AS childsource,`fnGetSrcContainerIDForItem`(obl.`item`) AS itemsource,
		obl.`remarks`, obl.`processloss`
        FROM
		(
		WITH RECURSIVE tree (batchnumber, item, childitem, quantity, productyield, planquantity, netrequired, grossrequired, minshelflife, lvl, remarks, processloss) AS
			(
				SELECT
					pldtl.`batchnumber`, 
					pt.`parentprod`, pt.`item`,
					pt.`quantity`, -- rcp quantity
					pt.`productyield`, -- rcp yield
					pldtl.`grossrequired` / 1, -- plan quantity
                    pldtl.`grossrequired` * pt.`quantity` / coalesce(`production`.`fnSTKgetItemYield`(pt.`item`), 1),  -- net required
                    pldtl.`grossrequired` * pt.`quantity` / coalesce(`production`.`fnSTKgetItemYield`(pt.`item`), 1) / `production`.`fnSTKgetItemProcessLoss`(pt.`item`), -- gross required
					`production`.`fnGetItemMinimumShelfLife`(pt.parentprod),
					prmStartLevel + 1 lvl, pldtl.`remarks`,
					`production`.`fnSTKgetItemProcessLoss`(pt.`item`)
				FROM tblproducttree pt JOIN `production`.`tblBOMaggregateMEMORY` AS pldtl ON pldtl.`childitem` = pt.`parentprod`
				WHERE pldtl.`childitem` = parentprod AND pldtl.`planmasterid` = prmPlanID AND pldtl.`lvl` = prmStartLevel
				UNION ALL
				SELECT
					cp.`batchnumber`,
					c.`parentprod`, c.`item`,
					c.`quantity`, -- rcp quantity
					c.`productyield`,  -- rcp yield
					cp.`grossrequired` / 1, -- plan quantity
                    cp.`grossrequired` * c.`quantity` / coalesce(`production`.`fnSTKgetItemYield`(c.`item`), 1),  -- net required
                    cp.`grossrequired` * c.`quantity` / coalesce(`production`.`fnSTKgetItemYield`(c.`item`), 1) / `production`.`fnSTKgetItemProcessLoss`(c.`item`), -- gross required
					`production`.`fnGetItemMinimumShelfLife`(c.parentprod),
					cp.`lvl` + 1, cp.`remarks`,
					`production`.`fnSTKgetItemProcessLoss`(c.`item`)
				FROM tree AS cp JOIN tblproducttree AS c ON cp.`childitem` = c.`parentprod`
			)
		SELECT
			tree.`lvl`, tree.`item`, tree.`batchnumber`, `fnGetProductName`(tree.`item`), tree.`childitem`, `fnGetProductName`(tree.`childitem`),
            tree.`quantity`, tree.`productyield`, tree.`planquantity`, tree.`minshelflife`,
			tree.`netrequired`, tree.`grossrequired`, tree.`remarks`, tree.`processloss`
		FROM tree
		ORDER BY tree.`lvl`, tree.`item`, tree.`batchnumber`, tree.`childitem`
		)
        obl
        GROUP BY
			obl.`lvl`, obl.`batchnumber`, obl.`childitem`, obl.`minshelflife`, obl.`netrequired`, obl.`grossrequired`,
			`fnGetSrcContainerIDForItem`(obl.`item`), obl.`remarks`, obl.`productyield`, obl.`processloss`
	) oblout
    INNER JOIN `tblproducts` tpd ON tpd.`id` = oblout.`childitem`
	GROUP BY
		lvl, batchnumber, childitem, minshelflife, childsource, itemsource, oblout.`remarks`, oblout.`productyield`, oblout.`processloss`
	ORDER BY oblout.`lvl`, oblout.`childitem`;

	-- REBUILD LEVELS ABOVE - SPLIT BY BATCHES
    
    /*
    -- 23 JULY 2025
	-- REBUILD LEVELS BELOW - SPLIT BY BATCHES

	SET sql_safe_updates = 0;
	DELETE FROM `production`.`tblBOMaggregateMEMORY` WHERE `id` > 0 AND `planmasterid` = prmPlanID AND `lvl` > prmStartLevel;
	SET sql_safe_updates = 1;

	INSERT INTO `production`.`tblBOMaggregateMEMORY`
		(
		 `user`, `lanusername`, `srcworkstation`, `srcworkstationip`,
		 `planmasterid`, `lvl`, `batchnumber`, `childitem`, `childitemreceipeversion`, `childitemname`, `childitemyield`, `minshelflife`, `planquantity`, 
		 `netrequired`, `grossrequired`, `processloss`, `srccontainer`, `destcontainer`, `stockatdestcontainer`,
         `overrideconsiderstock`, `overridebatchsplit`, `overridelastbatchfull`, `overrideMinStock`, `overrideMaxStock`,
         `remarks`
		)
	SELECT
		prmUser, prmLanUserName, prmSrcWorkStation, prmSrcWorkStationIP,
		prmplanid, oblout.`lvl`, oblout.`batchnumber`, oblout.`childitem`, `fnGetProductReceipeCodeVersion`(oblout.`childitem`), `fnGetProductName`(oblout.`childitem`),
		oblout.`productyield`, oblout.`minshelflife`,
		sum(oblout.`planquantity`) as `planquantity`,  
		sum(oblout.`netrequired`), sum(oblout.`grossrequired`),	avg(oblout.`processloss`), oblout.`childsource`, oblout.`itemsource`,
		`production`.`fnSTKitemStockAllAttributes`(
														1, 1, 'OVBSTK', 'ORLSTK', 'OITSTK',
														curdate(),
														oblout.`childitem`,
														`production`.`fnGetDestContainerIDForItem`(oblout.`childitem`),
														NULL,
														NULL,
														NULL,
														NULL,
														NULL,
														True,
														True
												   ),
		tpd.`genconsiderstockinplan`, tpd.`genfullbatches`, tpd.`unitaryweightalign`, tpd.`minstock`, tpd.`maxstock`,
        NULL
	FROM
		(
		SELECT
		prmplanid, obl.`lvl`, obl.`batchnumber`, obl.`childitem`, `fnGetProductName`(obl.`childitem`), obl.`productyield`, sum(obl.`planquantity`) as `planquantity`,
		obl.`minshelflife`,
		obl.`netrequired`, obl.`grossrequired`,	`fnGetSrcContainerIDForItem`(obl.`childitem`) AS childsource,`fnGetSrcContainerIDForItem`(obl.`item`) AS itemsource,
		obl.`remarks`, obl.`processloss`
        FROM
		(
		WITH RECURSIVE tree (batchnumber, item, childitem, quantity, productyield, planquantity, netrequired, grossrequired, minshelflife, lvl, remarks, processloss) AS
			(
				SELECT
					pldtl.`batchnumber`, 
					pt.`parentprod`, pt.`item`,
					pt.`quantity`, -- rcp quantity
					pt.`productyield`, -- rcp yield
					pldtl.`grossrequired` / 1, -- plan quantity
                    pldtl.`grossrequired` * pt.`quantity` / coalesce(`production`.`fnSTKgetItemYield`(pt.`item`), 1),  -- net required
                    pldtl.`grossrequired` * pt.`quantity` / coalesce(`production`.`fnSTKgetItemYield`(pt.`item`), 1) / `production`.`fnSTKgetItemProcessLoss`(pt.`item`), -- gross required
					`production`.`fnGetItemMinimumShelfLife`(pt.parentprod),
					prmStartLevel + 1 lvl, pldtl.`remarks`,
					`production`.`fnSTKgetItemProcessLoss`(pt.`item`)
				FROM tblproducttree pt JOIN `production`.`tblBOMaggregateMEMORY` AS pldtl ON pldtl.`childitem` = pt.`parentprod`
				WHERE pldtl.`childitem` = parentprod AND pldtl.`planmasterid` = prmPlanID AND pldtl.`lvl` = prmStartLevel
				UNION ALL
				SELECT
					cp.`batchnumber`,
					c.`parentprod`, c.`item`,
					c.`quantity`, -- rcp quantity
					c.`productyield`,  -- rcp yield
					cp.`grossrequired` / 1, -- plan quantity
                    cp.`grossrequired` * c.`quantity` / coalesce(`production`.`fnSTKgetItemYield`(c.`item`), 1),  -- net required
                    cp.`grossrequired` * c.`quantity` / coalesce(`production`.`fnSTKgetItemYield`(c.`item`), 1) / `production`.`fnSTKgetItemProcessLoss`(c.`item`), -- gross required
					`production`.`fnGetItemMinimumShelfLife`(c.parentprod),
					cp.`lvl` + 1, cp.`remarks`,
					`production`.`fnSTKgetItemProcessLoss`(c.`item`)
				FROM tree AS cp JOIN tblproducttree AS c ON cp.`childitem` = c.`parentprod`
			)
		SELECT
			tree.`lvl`, tree.`item`, tree.`batchnumber`, `fnGetProductName`(tree.`item`), tree.`childitem`, `fnGetProductName`(tree.`childitem`),
            tree.`quantity`, tree.`productyield`, tree.`planquantity`, tree.`minshelflife`,
			tree.`netrequired`, tree.`grossrequired`, tree.`remarks`, tree.`processloss`
		FROM tree
		ORDER BY tree.`lvl`, tree.`item`, tree.`batchnumber`, tree.`childitem`
		)
        obl
        GROUP BY
			obl.`lvl`, obl.`batchnumber`, obl.`childitem`, obl.`minshelflife`, obl.`netrequired`, obl.`grossrequired`,
			`fnGetSrcContainerIDForItem`(obl.`item`), obl.`remarks`, obl.`productyield`, obl.`processloss`
	) oblout
    INNER JOIN `tblproducts` tpd ON tpd.`id` = oblout.`childitem`
	GROUP BY
		lvl, batchnumber, childitem, minshelflife, childsource, itemsource, oblout.`remarks`, oblout.`productyield`, oblout.`processloss`
	ORDER BY oblout.`lvl`, oblout.`childitem`;

	-- REBUILD LEVELS ABOVE - SPLIT BY BATCHES
    */

END ;;

-- source: DROP PROCEDURE IF EXISTS `procSTKplanPickingListWithStock`
CREATE DEFINER=`root`@`%` PROCEDURE `procSTKplanPickingListWithStock`(IN prmSourceContainer INT)
BEGIN

	DECLARE crsDone BOOL DEFAULT false;
    DECLARE innerLoopDone BOOL DEFAULT false;
    
-- ##########  FETCH VARIABLES FOR ITEMS CURSORS  #######################################################################################################
    
	DECLARE crsPlanItemsJobsList TEXT;
    DECLARE crsPlanItemsBucketClosed TINYINT;
    DECLARE crsPlanItemsPlanJobID INT;
    DECLARE crsPlanItemsPlanID INT;
    DECLARE crsPlanItemsItemID INT;
    DECLARE crsPlanItemsItemVersion INT;
    DECLARE crsPlanItemsItemGrossRequired DECIMAL (16, 6);
    DECLARE crsPlanItemsItemSrcContainer INT;
    DECLARE crsPlanItemsItemDestContainer INT;
    DECLARE crsPlanItemsItemAllocatedQuantity DECIMAL (16, 6);
    DECLARE crsPlanItemsItemRunningSum DECIMAL (16, 6);
    DECLARE crsPlanItemsItemRequirementDelta DECIMAL (16, 6);
    DECLARE crsPlanItemsItemCheckSum DECIMAL (16, 6);
    
	DECLARE crsPlanItemsLocalLoopItemAllocatedQuantity DECIMAL (16, 6);
    DECLARE crsPlanItemsLocalLoopItemRunningSum DECIMAL (16, 6);
    DECLARE crsPlanItemsLocalLoopItemRequirementDelta DECIMAL (16, 6);
    DECLARE crsPlanItemsLocalLoopItemCheckSum DECIMAL (16, 6);
    
-- ##########  FETCH VARIABLES FOR STOCKS CURSORS  ######################################################################################################
	DECLARE stkCrsSweepAction VARCHAR(64);
    DECLARE stkCrsStockRowID INT;
    DECLARE stkCrsStockItem INT;
	DECLARE stkCrsStockItemUnit VARCHAR(16);
	DECLARE stkCrsStockDestContainer INT;
	DECLARE stkCrsStockItemRawQuantity DECIMAL (16, 6);
	DECLARE stkCrsStockItemNaturalUnits DECIMAL (16, 6);
	DECLARE stkCrsStockItemVersion INT;
	DECLARE stkCrsStockItemShapeFormat INT;
	DECLARE stkCrsStockItemProductionDate DATE;
	DECLARE stkCrsStockItemUseBy DATE;
	DECLARE stkCrsStockItemTraceNumber INT;
	DECLARE stkCrsStockItemMultiplier DECIMAL (16, 6);
	DECLARE stkCrsStockItemUnitConversionFactor DECIMAL (16, 6);
    
-- ##########  SUPPORT VARIABLES FOR STOCKS CURSORS  ####################################################################################################
    DECLARE stkCrsStockMode INT;
    
-- ##########  SUPPORT VARIABLES FOR STOCK CONSUMPTION LOOP  ############################################################################################

	DECLARE stkCrsItemStockAllocationComplete BOOLEAN;
	DECLARE crsPlanItemRunningSumInnerLocal DECIMAL (16, 6);

	DECLARE stkCrsItemStockAllocationInnerLocal DECIMAL (16, 6);
	DECLARE stkCrsItemRunningSumInnerLocal DECIMAL (16, 6);
	DECLARE stkCrsItemStockRequirementDeltaInnerLocal DECIMAL (16, 6);
    DECLARE stkCrsItemStockSpillOverPreviousInnerLocal DECIMAL (16, 6);
    DECLARE stkCrsItemStockSpillOverSumPreviousInnerLocal DECIMAL (16, 6);
    
    DECLARE stkCrsItemStockNeedStock DECIMAL (16, 6);
    DECLARE stkCrsStockRowAllocatedStock DECIMAL (16, 6);
    DECLARE stkCrsStockRowAllocatedStockRawQuantity DECIMAL (16, 6);
	DECLARE stkCrsStockRowItemAvailableStockRawQuantity DECIMAL (16, 6);
    DECLARE stkCrsStockRowItemAvailableStockNaturalQuantity DECIMAL (16, 6);
    
-- ##################################################################  CURSORS  #########################################################################
-- #############################################################  PICKING ITEMS CURSOR  #################################################################
	DECLARE crsPlanItemsOpenBucket CURSOR FOR
	SELECT
		iq.`planItemJobList`, -- crsPlanItemsJobsList
		iq.`closed`,  -- crsPlanItemsBucketClosed
		iq.`id`, -- crsPlanItemsPlanJobID
		iq.`planmasterid`, -- crsPlanItemsPlanID
		iq.`childitem`, -- crsPlanItemsItemID
		iq.`childitemreceipeversion`, -- crsPlanItemsItemVersion
		iq.`grossrequired`, -- crsPlanItemsItemGrossRequired
		iq.`srccontainer`, -- crsPlanItemsItemSrcContainer
		iq.`destcontainer`, -- crsPlanItemsItemDestContainer
		coalesce(iq.`stockItemAllocatedQuantity`, 0), -- crsPlanItemsItemAllocatedQuantity
		coalesce(iq.`stockItemRunningSum`, 0), -- crsPlanItemsItemRunningSum
		IF(isnull(iq.`stockItemStockRequirementDelta`), iq.`grossrequired`, iq.`stockItemStockRequirementDelta`),  -- crsPlanItemsItemRequirementDelta
		iq.`grossrequired` -
        (coalesce(iq.`stockItemRunningSum`, 0) + IF(isnull(iq.`stockItemStockRequirementDelta`), iq.`grossrequired`, iq.`stockItemStockRequirementDelta`))
        AS `checkSum` -- crsPlanItemsItemCheckSum
	FROM
		(
		SELECT
			concat('|  ', 'Plan: ', tba.`planmasterid`, ' - Job: ', tba.`id`, '  |') AS `planItemJobList`,
			tba.`id`,
			tba.`closed`,
			tba.`planmasterid`,
			tba.`childitem`,
			tba.`childitemreceipeversion`,
			tba.`grossrequired`,
			tba.`srccontainer`,
			tba.`destcontainer`,
			(
				SELECT
					sum(tpl.`stockItemAllocatedQuantity`)
				FROM
					`production`.`tblBOMaggregatePickingLists` tpl
				WHERE
					tpl.`planJobID` = tba.`id`
			) AS `stockItemAllocatedQuantity`,
			(
				SELECT
					max(tpl.`stockItemRunningSum`)
				FROM
					`production`.`tblBOMaggregatePickingLists` tpl
				WHERE
					tpl.`planJobID` = tba.`id`
			) AS `stockItemRunningSum`,
			(
				SELECT
					min(tpl.`stockItemStockRequirementDelta`)
				FROM
					`production`.`tblBOMaggregatePickingLists` tpl
				WHERE
					tpl.`planJobID` = tba.`id`
			) AS `stockItemStockRequirementDelta` 
		FROM
			`production`.`tblBOMaggregate` tba
		WHERE TRUE
			AND tba.`closed` = 0
			AND tba.`srccontainer` = prmSourceContainer
		) iq
	WHERE TRUE
		AND coalesce(iq.`stockItemRunningSum`, 0) < iq.`grossrequired`
		AND (isnull(iq.`stockItemStockRequirementDelta`) OR iq.`stockItemStockRequirementDelta` > 0)
	ORDER BY iq.`childitem`, iq.`grossrequired`;
    
-- #############################################################  CANDIDATE STOCK CURSOR  ###############################################################
    DECLARE crsStockRowsCandidateStock CURSOR FOR
	SELECT
		iq.`entryPoint`, -- stkCrsSweepAction
		iq.`stockRowID`, -- stkCrsStockRowID
		iq.`stockItemID`, -- stkCrsStockItem
		iq.`stockUnitID`, -- stkCrsStockItemUnit
		iq.`stockDestCOntainer`, -- stkCrsStockDestContainer
		iq.`stockRawQuantity`, -- stkCrsStockItemRawQuantity
		iq.`quantityNaturalUnits`, -- stkCrsStockItemNaturalUnits
		iq.`stockItemVersion`, -- stkCrsStockItemVersion
		iq.`stockItemShapeFormat`, -- stkCrsStockItemShapeFormat
		iq.`stockItemProductionDate`, -- stkCrsStockItemProductionDate
		iq.`stockItemUseBy`, -- stkCrsStockItemUseBy
		iq.`stockItemTraceNumber`, -- stkCrsStockItemTraceNumber
		iq.`stockItemShapeFormatMultiplier`, -- stkCrsStockItemMultiplier
		iq.`stockItemUnitConversionFactor`, -- stkCrsStockItemUnitConversionFactor
		iq.`stockRowItemAllocatedStock` as `stockRowAllocatedStock`, -- stkCrsStockRowAllocatedStock
		iq.`stockRawQuantity`
			- iq.`stockRowItemAllocatedStock` as `stockRowItemAvailableStockRawQuantity`, -- stkCrsStockRowItemAvailableStockRawQuantity
		iq.`quantityNaturalUnits`
			- (iq.`stockRowItemAllocatedStock` * iq.`stockItemShapeFormatMultiplier` * iq.`stockItemUnitConversionFactor`) as `stockRowItemAvailableStockNaturalQuantity` -- stkCrsStockRowItemAvailableStockNaturalQuantity
	FROM
	(
	SELECT
		'STOCK' as `entryPoint`, -- stkCrsSweepAction
		tsc.`id` as `stockRowID`, -- stkCrsStockRowID
		tsc.`item` as `stockItemID`, -- stkCrsStockItem
		tstkun.`unit` as `stockUnitID`, -- stkCrsStockItemUnit
		tsc.`destcontainer` as `stockDestCOntainer`, -- stkCrsStockDestContainer
		tsc.`quantity` as `stockRawQuantity`, -- stkCrsStockItemRawQuantity
		IF
			(
				isnull(tpm.`shapeFormat`),
				tsc.`quantity`,
				tsc.`quantity` * coalesce(tpm.`multiplier`, 1) * `production`.`fnGetUnitConversionFactor`(tpd.`purchasingunit`, tpd.`unit`)
			) as `quantityNaturalUnits`, -- stkCrsStockItemNaturalUnits
		tsc.`productreceipecodeversion` as `stockItemVersion`, -- stkCrsStockItemVersion
		tsc.`shapeformat` as `stockItemShapeFormat`, -- stkCrsStockItemShapeFormat
		tsc.`productiondate` as `stockItemProductionDate`, -- stkCrsStockItemProductionDate
		tsc.`useby` as `stockItemUseBy`, -- stkCrsStockItemUseBy
		tsc.`tracenumber` as `stockItemTraceNumber`, -- stkCrsStockItemTraceNumber
		coalesce(tpm.`multiplier`, 1) as `stockItemShapeFormatMultiplier`, -- stkCrsStockItemMultiplier
		IF (not isnull(tpm.`shapeFormat`), `production`.`fnGetUnitConversionFactor`(tpd.`purchasingunit`, tpd.`unit`), 1) as `stockItemUnitConversionFactor`, -- stkCrsStockItemUnitConversionFactor
		(
			SELECT
				coalesce(sum(coalesce(tpli.`stockItemAllocatedQuantity`, 0)), 0)
			FROM
				`production`.`tblBOMaggregatePickingLists` tpli
			WHERE tpli.`stockCacheRowID` = tsc.`id`
		) as `stockRowItemAllocatedStock`
	FROM `production`.`tblstockcache` tsc
	INNER JOIN `production`.`tblproducts` tpd ON tpd.`id` = tsc.`item`
	INNER JOIN `production`.tblContainers tcn ON tcn.`id` = tsc.`destcontainer`
	INNER JOIN `production`.`tblunits` tstkun ON tstkun.`unit` = tsc.`unit`
	LEFT JOIN `production`.tblProductsMappingSupplier tpm ON tpm.`item` = tsc.`item` AND tpm.`id` = tsc.`shapeformat`
	WHERE TRUE
	AND tsc.`item` = crsPlanItemsItemID -- crsPlanItemsItemID
	AND tsc.`productreceipecodeversion` <=> crsPlanItemsItemVersion -- crsPlanItemsItemVersion
	AND
		CASE
			WHEN stkCrsStockMode = 1 THEN isnull(tsc.`shapeformat`) -- stkCrsStockMode
			WHEN stkCrsStockMode = 2 THEN not isnull(tsc.`shapeformat`) -- stkCrsStockMode
		END
	AND tsc.`destcontainer` = prmSourceContainer -- ENTRY PARAMETER - STOCK LOCATION --> prmSourceContainer
	AND tcn.`internal` = -1
	AND tcn.`realstock` = -1
	AND
		(
			(
				-- ## BELOW FOR ROWS USED AND FULLY EXHAUSTED ##
				(tsc.`item`, tsc.`productreceipecodeversion`, tsc.`shapeformat`, tsc.`destcontainer`, tsc.`productiondate`, tsc.`useby`, tsc.`tracenumber`, -1)
					NOT IN (
								SELECT
									tpli.`stockItem`,
									tpli.`stockItemVersion`,
									tpli.`stockItemShapeFormat`,
									tpli.`planRequirementSource`,
									tpli.`stockItemProductionDate`,
									tpli.`stockItemUseBy`,
									tpli.`stockItemTraceNumber`,
									tpli.`stockCacheRowConsumed`
								FROM `production`.`tblBOMaggregatePickingLists` tpli
							)
			)
		OR
			(
				-- ## BELOW FOR ROWS USED AND PARTIALLY EXHAUSTED ##
				(tsc.`item`, tsc.`productreceipecodeversion`, tsc.`shapeformat`, tsc.`destcontainer`, tsc.`productiondate`, tsc.`useby`, tsc.`tracenumber`, 0)
					IN (
								SELECT
									tpli.`stockItem`,
									tpli.`stockItemVersion`,
									tpli.`stockItemShapeFormat`,
									tpli.`planRequirementSource`,
									tpli.`stockItemProductionDate`,
									tpli.`stockItemUseBy`,
									tpli.`stockItemTraceNumber`,
									tpli.`stockCacheRowConsumed`
								FROM `production`.`tblBOMaggregatePickingLists` tpli
							)
			)
		)
	) iq
	ORDER BY iq.`stockItemUseBy`, iq.`stockItemTraceNumber`, iq.`quantityNaturalUnits`;
    
	DECLARE CONTINUE HANDLER FOR SQLSTATE '02000' SET crsDone = TRUE;
    
-- ####################################################################################################################################################################
-- ####################################################################################################################################################################
-- #######################################################  FIND SMALL BUCKETS AND TRY TO FILL WITH LOOSE STOCK  ######################################################

    SET crsDone = FALSE;
    OPEN crsPlanItemsOpenBucket; -- LOOP THROUGH ITEMS IN PLAN OPEN ROWS FOR PICKING LIST - CONSUME ONLY COMPLETE ROWS
    
    WHILE NOT crsDone DO

		FETCH crsPlanItemsOpenBucket INTO
			crsPlanItemsJobsList, crsPlanItemsBucketClosed, crsPlanItemsPlanJobID, crsPlanItemsPlanID,
			crsPlanItemsItemID, crsPlanItemsItemVersion, crsPlanItemsItemGrossRequired,
			crsPlanItemsItemSrcContainer, crsPlanItemsItemDestContainer,
			crsPlanItemsItemAllocatedQuantity, crsPlanItemsItemRunningSum, crsPlanItemsItemRequirementDelta, crsPlanItemsItemCheckSum;
		IF NOT crsDone THEN
			SET crsDone = FALSE;
            
			SET crsPlanItemsLocalLoopItemAllocatedQuantity = crsPlanItemsItemAllocatedQuantity;
			SET crsPlanItemsLocalLoopItemRunningSum = crsPlanItemsItemRunningSum;
			SET crsPlanItemsLocalLoopItemRequirementDelta = crsPlanItemsItemRequirementDelta;
			SET crsPlanItemsLocalLoopItemCheckSum = crsPlanItemsItemCheckSum;
			
			SET stkCrsItemStockAllocationInnerLocal = 0;
			SET stkCrsItemStockRequirementDeltaInnerLocal = crsPlanItemsItemRunningSum;
			SET stkCrsItemRunningSumInnerLocal = crsPlanItemsItemRequirementDelta;

			SET stkCrsStockMode = 1;
			OPEN crsStockRowsCandidateStock; -- LOOP THROUGH ITEMS IN PLAN OPEN ROWS FOR PICKING LIST - CONSUME PARTIAL ROWS
			
			WHILE NOT crsDone DO

				FETCH crsStockRowsCandidateStock INTO
					stkCrsSweepAction, stkCrsStockRowID,
					stkCrsStockItem, stkCrsStockItemUnit, stkCrsStockDestContainer,
					stkCrsStockItemRawQuantity, stkCrsStockItemNaturalUnits,
					stkCrsStockItemVersion, stkCrsStockItemShapeFormat,
					stkCrsStockItemProductionDate, stkCrsStockItemUseBy, stkCrsStockItemTraceNumber,
					stkCrsStockItemMultiplier, stkCrsStockItemUnitConversionFactor,
                    stkCrsStockRowAllocatedStock, stkCrsStockRowItemAvailableStockRawQuantity, stkCrsStockRowItemAvailableStockNaturalQuantity;

				IF NOT crsDone THEN
                
					IF TRUE
						AND stkCrsStockRowItemAvailableStockNaturalQuantity > crsPlanItemsItemRequirementDelta
                        AND stkCrsItemStockAllocationInnerLocal < crsPlanItemsItemRequirementDelta
					THEN
						SET stkCrsItemStockAllocationInnerLocal = crsPlanItemsItemRequirementDelta;
                        SET stkCrsItemStockRequirementDeltaInnerLocal = 0;
                        SET stkCrsItemRunningSumInnerLocal = crsPlanItemsItemRequirementDelta;

						INSERT INTO `production`.`tblBOMaggregatePickingLists`
							(
								`entrySource`, `planID`, `planJobID`, `planJobIDlist`, 
								`planItemVersion`, `planItemRequirement`,
								`planRequirementSource`, `planRequirementConsumer`, 
								`stockCacheRowID`, `stockCacheRowConsumed`, `stockItem`, `stockItemVersion`,
								`stockItemShapeFormat`, `stockItemMultiplier`, `stockItemUnitConversion`,
								`stockItemPlanRequirement`,
								`stockItemNaturalUnitsQuantity`,
								`stockItemRawQuantity`,
								`stockItemAllocatedQuantity`,
								`stockItemAllocatedUnits`,
								`stockItemRunningSum`,
								`stockItemStockRequirementDelta`,
								`stockItemDestContainer`, `stockItemProductionDate`, `stockItemUseBy`, `stockItemTraceNumber`
							)
						VALUES
							(
								concat(stkCrsSweepAction, '#PARTIAL-ROW#1'), crsPlanItemsPlanID, crsPlanItemsPlanJobID, crsPlanItemsJobsList,
								crsPlanItemsItemVersion, crsPlanItemsItemGrossRequired,
								crsPlanItemsItemSrcContainer, crsPlanItemsItemDestContainer,
								stkCrsStockRowID, 0, stkCrsStockItem, stkCrsStockItemVersion,
								stkCrsStockItemShapeFormat, stkCrsStockItemMultiplier, stkCrsStockItemUnitConversionFactor,
								crsPlanItemsItemGrossRequired,
								stkCrsStockItemNaturalUnits,
								stkCrsStockItemRawQuantity,
								crsPlanItemsItemRequirementDelta,
								stkCrsStockItemUnit,
								crsPlanItemsItemRequirementDelta,
								0,
								stkCrsStockDestContainer, stkCrsStockItemProductionDate, stkCrsStockItemUseBy, stkCrsStockItemTraceNumber
							)
						ON DUPLICATE KEY UPDATE
							`entrySource` = concat(stkCrsSweepAction, '#PARTIAL-ROW#1'),
							`planID` = crsPlanItemsPlanID,
							`planJobID` = crsPlanItemsPlanJobID,
							`planJobIDlist` = crsPlanItemsJobsList, 
							`planItemVersion` = crsPlanItemsItemVersion,
							`planItemRequirement` = crsPlanItemsItemGrossRequired,
							`planRequirementSource` = crsPlanItemsItemSrcContainer,
							`planRequirementConsumer` = crsPlanItemsItemDestContainer, 
							`stockCacheRowID` = stkCrsStockRowID,
							`stockCacheRowConsumed` = 0,
							`stockItem` = stkCrsStockItem,
							`stockItemVersion` = stkCrsStockItemVersion,
							`stockItemShapeFormat` = stkCrsStockItemShapeFormat,
							`stockItemMultiplier` = stkCrsStockItemMultiplier,
							`stockItemUnitConversion` = stkCrsStockItemUnitConversionFactor,
							`stockItemPlanRequirement` = crsPlanItemsItemGrossRequired,
							`stockItemNaturalUnitsQuantity` = stkCrsStockItemNaturalUnits,
							`stockItemRawQuantity` = stkCrsStockItemRawQuantity,
							`stockItemAllocatedQuantity` = stkCrsItemStockAllocationInnerLocal,
							`stockItemAllocatedUnits` = stkCrsStockItemUnit,
							`stockItemRunningSum` = stkCrsItemRunningSumInnerLocal,
							`stockItemRunningSumSpillOver` = 0,
							`stockItemStockRequirementDelta` = stkCrsItemStockRequirementDeltaInnerLocal, 
							`stockItemDestContainer` = stkCrsStockDestContainer,
							`stockItemProductionDate` = stkCrsStockItemProductionDate,
							`stockItemUseBy` = stkCrsStockItemUseBy,
							`stockItemTraceNumber` =  stkCrsStockItemTraceNumber;
					END IF;
				END IF;

			END WHILE;

			CLOSE crsStockRowsCandidateStock; -- LOOP THROUGH ITEMS IN PLAN OPEN ROWS FOR PICKING LIST
			SET crsDone = FALSE;
        
		END IF;

	END WHILE;

	CLOSE crsPlanItemsOpenBucket; -- LOOP THROUGH ITEMS IN PLAN OPEN ROWS FOR PICKING LIST
    SET crsDone = FALSE;
-- #######################################################  FIND SMALL BUCKETS AND TRY TO FILL WITH LOOSE STOCK  ######################################################
-- ####################################################################################################################################################################
-- ######################################  FIND LARGER BUCKETS AND TRY TO FILL WITH LOOSE STOCK USING ALL AVAILABLE LOOSE STOCK  ######################################

    SET crsDone = FALSE;
    OPEN crsPlanItemsOpenBucket; -- LOOP THROUGH ITEMS IN PLAN OPEN ROWS FOR PICKING LIST - CONSUME ONLY COMPLETE ROWS
    
    WHILE NOT crsDone DO

		FETCH crsPlanItemsOpenBucket INTO
			crsPlanItemsJobsList, crsPlanItemsBucketClosed, crsPlanItemsPlanJobID, crsPlanItemsPlanID,
			crsPlanItemsItemID, crsPlanItemsItemVersion, crsPlanItemsItemGrossRequired,
			crsPlanItemsItemSrcContainer, crsPlanItemsItemDestContainer,
			crsPlanItemsItemAllocatedQuantity, crsPlanItemsItemRunningSum, crsPlanItemsItemRequirementDelta, crsPlanItemsItemCheckSum;
		IF NOT crsDone THEN

			SET crsDone = FALSE;
			
			SET crsPlanItemsLocalLoopItemAllocatedQuantity = coalesce(crsPlanItemsItemAllocatedQuantity, 0);
			SET crsPlanItemsLocalLoopItemRunningSum = coalesce(crsPlanItemsItemRunningSum, 0);
			SET crsPlanItemsLocalLoopItemRequirementDelta = coalesce(crsPlanItemsItemRequirementDelta, 0);

			SET stkCrsStockMode = 1;
			OPEN crsStockRowsCandidateStock; -- LOOP THROUGH ITEMS IN PLAN OPEN ROWS FOR PICKING LIST - CONSUME ONLY COMPLETE ROWS
			
			WHILE NOT crsDone DO

				FETCH crsStockRowsCandidateStock INTO
					stkCrsSweepAction, stkCrsStockRowID,
					stkCrsStockItem, stkCrsStockItemUnit, stkCrsStockDestContainer,
					stkCrsStockItemRawQuantity, stkCrsStockItemNaturalUnits,
					stkCrsStockItemVersion, stkCrsStockItemShapeFormat,
					stkCrsStockItemProductionDate, stkCrsStockItemUseBy, stkCrsStockItemTraceNumber,
					stkCrsStockItemMultiplier, stkCrsStockItemUnitConversionFactor,
                    stkCrsStockRowAllocatedStock, stkCrsStockRowItemAvailableStockRawQuantity, stkCrsStockRowItemAvailableStockNaturalQuantity;
				IF NOT crsDone THEN

					IF TRUE
						AND stkCrsStockRowItemAvailableStockNaturalQuantity > crsPlanItemsItemRequirementDelta
                        AND stkCrsItemStockAllocationInnerLocal < crsPlanItemsItemRequirementDelta
					THEN

						INSERT INTO `production`.`tblBOMaggregatePickingLists`
							(
								`entrySource`, `planID`, `planJobID`, `planJobIDlist`, 
								`planItemVersion`, `planItemRequirement`,
								`planRequirementSource`, `planRequirementConsumer`, 
								`stockCacheRowID`, `stockCacheRowConsumed`, `stockItem`, `stockItemVersion`,
								`stockItemShapeFormat`, `stockItemMultiplier`, `stockItemUnitConversion`,
								`stockItemPlanRequirement`,
								`stockItemNaturalUnitsQuantity`,
								`stockItemRawQuantity`,
								`stockItemAllocatedQuantity`,
								`stockItemAllocatedUnits`,
								`stockItemRunningSum`,
								`stockItemStockRequirementDelta`,
								`stockItemDestContainer`, `stockItemProductionDate`, `stockItemUseBy`, `stockItemTraceNumber`
							)
						VALUES
							(
								concat(stkCrsSweepAction, '#PARTIAL-ROW#2'), crsPlanItemsPlanID, crsPlanItemsPlanJobID, crsPlanItemsJobsList,
								crsPlanItemsItemVersion, crsPlanItemsItemGrossRequired,
								crsPlanItemsItemSrcContainer, crsPlanItemsItemDestContainer,
								stkCrsStockRowID, 0, stkCrsStockItem, stkCrsStockItemVersion,
								stkCrsStockItemShapeFormat, stkCrsStockItemMultiplier, stkCrsStockItemUnitConversionFactor,
								crsPlanItemsItemGrossRequired,
								stkCrsStockItemNaturalUnits,
								stkCrsStockItemRawQuantity,
								crsPlanItemsItemAllocatedQuantity,
								stkCrsStockItemUnit,
								crsPlanItemsItemRunningSum + crsPlanItemsItemAllocatedQuantity,
								0,
								stkCrsStockDestContainer, stkCrsStockItemProductionDate, stkCrsStockItemUseBy, stkCrsStockItemTraceNumber
							)
						ON DUPLICATE KEY UPDATE
							`entrySource` = concat(stkCrsSweepAction, '#PARTIAL-ROW#2'),
							`planID` = crsPlanItemsPlanID,
							`planJobID` = crsPlanItemsPlanJobID,
							`planJobIDlist` = crsPlanItemsJobsList, 
							`planItemVersion` = crsPlanItemsItemVersion,
							`planItemRequirement` = crsPlanItemsItemGrossRequired,
							`planRequirementSource` = crsPlanItemsItemSrcContainer,
							`planRequirementConsumer` = crsPlanItemsItemDestContainer, 
							`stockCacheRowID` = stkCrsStockRowID,
							`stockCacheRowConsumed` = 0,
							`stockItem` = stkCrsStockItem,
							`stockItemVersion` = stkCrsStockItemVersion,
							`stockItemShapeFormat` = stkCrsStockItemShapeFormat,
							`stockItemMultiplier` = stkCrsStockItemMultiplier,
							`stockItemUnitConversion` = stkCrsStockItemUnitConversionFactor,
							`stockItemPlanRequirement` = crsPlanItemsItemGrossRequired,
							`stockItemNaturalUnitsQuantity` = stkCrsStockItemNaturalUnits,
							`stockItemRawQuantity` = stkCrsStockItemRawQuantity,
							`stockItemAllocatedQuantity` = crsPlanItemsItemAllocatedQuantity,
							`stockItemAllocatedUnits` = stkCrsStockItemUnit,
							`stockItemRunningSum` = crsPlanItemsItemRunningSum + crsPlanItemsItemAllocatedQuantity,
							`stockItemRunningSumSpillOver` = 0,
							`stockItemStockRequirementDelta` = 0, 
							`stockItemDestContainer` = stkCrsStockDestContainer,
							`stockItemProductionDate` = stkCrsStockItemProductionDate,
							`stockItemUseBy` = stkCrsStockItemUseBy,
							`stockItemTraceNumber` =  stkCrsStockItemTraceNumber;
					ELSE
                    
						SET crsPlanItemsLocalLoopItemAllocatedQuantity = coalesce(crsPlanItemsItemAllocatedQuantity, 0);
						SET crsPlanItemsLocalLoopItemRunningSum = coalesce(crsPlanItemsLocalLoopItemRunningSum, 0) + coalesce(stkCrsStockRowItemAvailableStockNaturalQuantity, 0);
						SET crsPlanItemsLocalLoopItemRequirementDelta = coalesce(crsPlanItemsLocalLoopItemRequirementDelta, 0) - coalesce(stkCrsStockRowItemAvailableStockNaturalQuantity, 0);

						INSERT INTO `production`.`tblBOMaggregatePickingLists`
							(
								`entrySource`, `planID`, `planJobID`, `planJobIDlist`, 
								`planItemVersion`, `planItemRequirement`,
								`planRequirementSource`, `planRequirementConsumer`, 
								`stockCacheRowID`, `stockCacheRowConsumed`, `stockItem`, `stockItemVersion`,
								`stockItemShapeFormat`, `stockItemMultiplier`, `stockItemUnitConversion`,
								`stockItemPlanRequirement`,
								`stockItemNaturalUnitsQuantity`,
								`stockItemRawQuantity`,
								`stockItemAllocatedQuantity`,
								`stockItemAllocatedUnits`,
								`stockItemRunningSum`,
								`stockItemStockRequirementDelta`,
								`stockItemDestContainer`, `stockItemProductionDate`, `stockItemUseBy`, `stockItemTraceNumber`
							)
						VALUES
							(
								concat(stkCrsSweepAction, '#FULL-ROW#2'), crsPlanItemsPlanID, crsPlanItemsPlanJobID, crsPlanItemsJobsList,
								crsPlanItemsItemVersion, crsPlanItemsItemGrossRequired,
								crsPlanItemsItemSrcContainer, crsPlanItemsItemDestContainer,
								stkCrsStockRowID, 1, stkCrsStockItem, stkCrsStockItemVersion,
								stkCrsStockItemShapeFormat, stkCrsStockItemMultiplier, stkCrsStockItemUnitConversionFactor,
								crsPlanItemsItemGrossRequired,
								stkCrsStockItemNaturalUnits,
								stkCrsStockRowItemAvailableStockNaturalQuantity,
								stkCrsStockRowItemAvailableStockNaturalQuantity,
								stkCrsStockItemUnit,
								crsPlanItemsLocalLoopItemRunningSum,
								crsPlanItemsLocalLoopItemRequirementDelta,
								stkCrsStockDestContainer, stkCrsStockItemProductionDate, stkCrsStockItemUseBy, stkCrsStockItemTraceNumber
							)
						ON DUPLICATE KEY UPDATE
							`entrySource` = concat(stkCrsSweepAction, '#FULL-ROW#2'),
							`planID` = crsPlanItemsPlanID,
							`planJobID` = crsPlanItemsPlanJobID,
							`planJobIDlist` = crsPlanItemsJobsList, 
							`planItemVersion` = crsPlanItemsItemVersion,
							`planItemRequirement` = crsPlanItemsItemGrossRequired,
							`planRequirementSource` = crsPlanItemsItemSrcContainer,
							`planRequirementConsumer` = crsPlanItemsItemDestContainer, 
							`stockCacheRowID` = stkCrsStockRowID,
							`stockCacheRowConsumed` = 1,
							`stockItem` = stkCrsStockItem,
							`stockItemVersion` = stkCrsStockItemVersion,
							`stockItemShapeFormat` = stkCrsStockItemShapeFormat,
							`stockItemMultiplier` = stkCrsStockItemMultiplier,
							`stockItemUnitConversion` = stkCrsStockItemUnitConversionFactor,
							`stockItemPlanRequirement` = crsPlanItemsItemGrossRequired,
							`stockItemNaturalUnitsQuantity` = stkCrsStockItemNaturalUnits,
							`stockItemRawQuantity` = stkCrsItemStockAllocationInnerLocal,
							`stockItemAllocatedQuantity` = crsPlanItemsItemRequirementDelta,
							`stockItemAllocatedUnits` = stkCrsStockItemUnit,
							`stockItemRunningSum` = stkCrsItemRunningSumInnerLocal,
							`stockItemRunningSumSpillOver` = 0,
							`stockItemStockRequirementDelta` = stkCrsItemStockRequirementDeltaInnerLocal, 
							`stockItemDestContainer` = stkCrsStockDestContainer,
							`stockItemProductionDate` = stkCrsStockItemProductionDate,
							`stockItemUseBy` = stkCrsStockItemUseBy,
							`stockItemTraceNumber` =  stkCrsStockItemTraceNumber;
                            
							UPDATE
								`production`.`tblBOMaggregatePickingLists` tpl
							SET
								tpl.`stockCacheRowConsumed` = -1
							WHERE TRUE
								AND tpl.`id` > 0
								AND tpl.`stockCacheRowID` = stkCrsStockRowID;
					END IF;
				END IF;

			END WHILE;

			CLOSE crsStockRowsCandidateStock; -- LOOP THROUGH ITEMS IN PLAN OPEN ROWS FOR PICKING LIST
			SET crsDone = FALSE;
        
		END IF;

	END WHILE;

	CLOSE crsPlanItemsOpenBucket; -- LOOP THROUGH ITEMS IN PLAN OPEN ROWS FOR PICKING LIST
    SET crsDone = FALSE;

-- ######################################  FIND LARGER BUCKETS AND TRY TO FILL WITH LOOSE STOCK USING ALL AVAILABLE LOOSE STOCK  ######################################
-- ####################################################################################################################################################################
-- ####################################################################################################################################################################
-- #################################  FIND LARGER BUCKETS AND TRY TO FILL WITH SHAPE AND FORMAT STOCK USING ALL AVAILABLE STOCK  ######################################

    SET crsDone = FALSE;
    OPEN crsPlanItemsOpenBucket; -- LOOP THROUGH ITEMS IN PLAN OPEN ROWS FOR PICKING LIST - CONSUME ONLY COMPLETE ROWS
    
    WHILE NOT crsDone DO

		FETCH crsPlanItemsOpenBucket INTO
			crsPlanItemsJobsList, crsPlanItemsBucketClosed, crsPlanItemsPlanJobID, crsPlanItemsPlanID,
			crsPlanItemsItemID, crsPlanItemsItemVersion, crsPlanItemsItemGrossRequired,
			crsPlanItemsItemSrcContainer, crsPlanItemsItemDestContainer,
			crsPlanItemsItemAllocatedQuantity, crsPlanItemsItemRunningSum, crsPlanItemsItemRequirementDelta, crsPlanItemsItemCheckSum;
		IF NOT crsDone THEN

			SET crsDone = FALSE;
			
			SET crsPlanItemsLocalLoopItemAllocatedQuantity = coalesce(crsPlanItemsItemAllocatedQuantity, 0);
			SET crsPlanItemsLocalLoopItemRunningSum = coalesce(crsPlanItemsItemRunningSum, 0);
			SET crsPlanItemsLocalLoopItemRequirementDelta = coalesce(crsPlanItemsItemRequirementDelta, 0);
            SET stkCrsItemStockAllocationComplete = FALSE;

			SET stkCrsStockMode = 2;
			OPEN crsStockRowsCandidateStock; -- LOOP THROUGH ITEMS IN PLAN OPEN ROWS FOR PICKING LIST - CONSUME ONLY SHAPE AND FORMAT STOCK
			
			WHILE NOT crsDone DO

				FETCH crsStockRowsCandidateStock INTO
					stkCrsSweepAction, stkCrsStockRowID,
					stkCrsStockItem, stkCrsStockItemUnit, stkCrsStockDestContainer,
					stkCrsStockItemRawQuantity, stkCrsStockItemNaturalUnits,
					stkCrsStockItemVersion, stkCrsStockItemShapeFormat,
					stkCrsStockItemProductionDate, stkCrsStockItemUseBy, stkCrsStockItemTraceNumber,
					stkCrsStockItemMultiplier, stkCrsStockItemUnitConversionFactor,
                    stkCrsStockRowAllocatedStock, stkCrsStockRowItemAvailableStockRawQuantity, stkCrsStockRowItemAvailableStockNaturalQuantity;
				IF NOT crsDone THEN
                
				SET stkCrsItemStockNeedStock = crsPlanItemsLocalLoopItemRequirementDelta / (1 * stkCrsStockItemMultiplier * stkCrsStockItemUnitConversionFactor);

					IF TRUE
						AND stkCrsStockRowItemAvailableStockRawQuantity > stkCrsItemStockNeedStock
                        AND NOT stkCrsItemStockAllocationComplete
					THEN

						INSERT INTO `production`.`tblBOMaggregatePickingLists`
							(
								`entrySource`, `planID`, `planJobID`, `planJobIDlist`, 
								`planItemVersion`, `planItemRequirement`,
								`planRequirementSource`, `planRequirementConsumer`, 
								`stockCacheRowID`, `stockCacheRowConsumed`, `stockItem`, `stockItemVersion`,
								`stockItemShapeFormat`, `stockItemMultiplier`, `stockItemUnitConversion`,
								`stockItemPlanRequirement`,
								`stockItemNaturalUnitsQuantity`,
								`stockItemRawQuantity`,
								`stockItemAllocatedQuantity`,
								`stockItemAllocatedUnits`,
								`stockItemRunningSum`,
								`stockItemStockRequirementDelta`,
								`stockItemDestContainer`, `stockItemProductionDate`, `stockItemUseBy`, `stockItemTraceNumber`
							)
						VALUES
							(
								concat(stkCrsSweepAction, '#PARTIAL-SHPFMT#1'), crsPlanItemsPlanID, crsPlanItemsPlanJobID, crsPlanItemsJobsList,
								crsPlanItemsItemVersion, crsPlanItemsItemGrossRequired,
								crsPlanItemsItemSrcContainer, crsPlanItemsItemDestContainer,
								stkCrsStockRowID, 0, stkCrsStockItem, stkCrsStockItemVersion,
								stkCrsStockItemShapeFormat, stkCrsStockItemMultiplier, stkCrsStockItemUnitConversionFactor,
								crsPlanItemsItemGrossRequired,
								stkCrsStockItemNaturalUnits,
								stkCrsStockItemRawQuantity,
								ceiling(stkCrsItemStockNeedStock),
								stkCrsStockItemUnit,
								crsPlanItemsItemGrossRequired,
								0,
								stkCrsStockDestContainer, stkCrsStockItemProductionDate, stkCrsStockItemUseBy, stkCrsStockItemTraceNumber
							)
						ON DUPLICATE KEY UPDATE
							`entrySource` = concat(stkCrsSweepAction, '#PARTIAL-SHPFMT#1'),
							`planID` = crsPlanItemsPlanID,
							`planJobID` = crsPlanItemsPlanJobID,
							`planJobIDlist` = crsPlanItemsJobsList, 
							`planItemVersion` = crsPlanItemsItemVersion,
							`planItemRequirement` = crsPlanItemsItemGrossRequired,
							`planRequirementSource` = crsPlanItemsItemSrcContainer,
							`planRequirementConsumer` = crsPlanItemsItemDestContainer, 
							`stockCacheRowID` = stkCrsStockRowID,
							`stockCacheRowConsumed` = 0,
							`stockItem` = stkCrsStockItem,
							`stockItemVersion` = stkCrsStockItemVersion,
							`stockItemShapeFormat` = stkCrsStockItemShapeFormat,
							`stockItemMultiplier` = stkCrsStockItemMultiplier,
							`stockItemUnitConversion` = stkCrsStockItemUnitConversionFactor,
							`stockItemPlanRequirement` = crsPlanItemsItemGrossRequired,
							`stockItemNaturalUnitsQuantity` = stkCrsStockItemNaturalUnits,
							`stockItemRawQuantity` = stkCrsStockItemRawQuantity,
							`stockItemAllocatedQuantity` = ceiling(stkCrsItemStockNeedStock), -- stkCrsItemStockNeedStock,
							`stockItemAllocatedUnits` = stkCrsStockItemUnit,
							`stockItemRunningSum` = crsPlanItemsItemGrossRequired,
							`stockItemRunningSumSpillOver` = 0,
							`stockItemStockRequirementDelta` = 0, 
							`stockItemDestContainer` = stkCrsStockDestContainer,
							`stockItemProductionDate` = stkCrsStockItemProductionDate,
							`stockItemUseBy` = stkCrsStockItemUseBy,
							`stockItemTraceNumber` =  stkCrsStockItemTraceNumber;
                            
							SET crsPlanItemsLocalLoopItemAllocatedQuantity = crsPlanItemsLocalLoopItemAllocatedQuantity + 0;
							SET crsPlanItemsLocalLoopItemRunningSum = crsPlanItemsLocalLoopItemRunningSum + 0;
							SET crsPlanItemsLocalLoopItemRequirementDelta = crsPlanItemsLocalLoopItemRequirementDelta - 0;
							SET stkCrsItemStockAllocationComplete = TRUE;

					ELSE
                    
						IF NOT stkCrsItemStockAllocationComplete THEN
							SET crsPlanItemsLocalLoopItemAllocatedQuantity = coalesce(crsPlanItemsLocalLoopItemAllocatedQuantity, 0) +
								stkCrsStockRowItemAvailableStockRawQuantity * stkCrsStockItemMultiplier * stkCrsStockItemUnitConversionFactor;
							SET crsPlanItemsLocalLoopItemRunningSum = coalesce(crsPlanItemsLocalLoopItemRunningSum, 0) +
								stkCrsStockRowItemAvailableStockRawQuantity * stkCrsStockItemMultiplier * stkCrsStockItemUnitConversionFactor;
							SET crsPlanItemsLocalLoopItemRequirementDelta = coalesce(crsPlanItemsLocalLoopItemRequirementDelta, 0) -
								stkCrsStockRowItemAvailableStockRawQuantity  * stkCrsStockItemMultiplier * stkCrsStockItemUnitConversionFactor;
                                
							SET stkCrsStockRowItemAvailableStockRawQuantity = ceiling(stkCrsStockRowItemAvailableStockRawQuantity);

							INSERT INTO `production`.`tblBOMaggregatePickingLists`
							(
								`entrySource`, `planID`, `planJobID`, `planJobIDlist`, 
								`planItemVersion`, `planItemRequirement`,
								`planRequirementSource`, `planRequirementConsumer`, 
								`stockCacheRowID`, `stockCacheRowConsumed`, `stockItem`, `stockItemVersion`,
								`stockItemShapeFormat`, `stockItemMultiplier`, `stockItemUnitConversion`,
								`stockItemPlanRequirement`,
								`stockItemNaturalUnitsQuantity`,
								`stockItemRawQuantity`,
								`stockItemAllocatedQuantity`,
								`stockItemAllocatedUnits`,
								`stockItemRunningSum`,
								`stockItemStockRequirementDelta`,
								`stockItemDestContainer`, `stockItemProductionDate`, `stockItemUseBy`, `stockItemTraceNumber`
							)
							VALUES
								(
									concat(stkCrsSweepAction, '#FULL-SHPFMT#2'), crsPlanItemsPlanID, crsPlanItemsPlanJobID, crsPlanItemsJobsList,
									crsPlanItemsItemVersion, crsPlanItemsItemGrossRequired,
									crsPlanItemsItemSrcContainer, crsPlanItemsItemDestContainer,
									stkCrsStockRowID, 1, stkCrsStockItem, stkCrsStockItemVersion,
									stkCrsStockItemShapeFormat, stkCrsStockItemMultiplier, stkCrsStockItemUnitConversionFactor,
									crsPlanItemsItemGrossRequired,
									stkCrsStockItemNaturalUnits,
									stkCrsStockRowItemAvailableStockRawQuantity,
									stkCrsStockRowItemAvailableStockRawQuantity,
									stkCrsStockItemUnit,
									crsPlanItemsLocalLoopItemRunningSum,
									crsPlanItemsLocalLoopItemRequirementDelta,
									stkCrsStockDestContainer, stkCrsStockItemProductionDate, stkCrsStockItemUseBy, stkCrsStockItemTraceNumber
								)
							ON DUPLICATE KEY UPDATE
								`entrySource` = concat(stkCrsSweepAction, '#FULL-SHPFMT#2'),
								`planID` = crsPlanItemsPlanID,
								`planJobID` = crsPlanItemsPlanJobID,
								`planJobIDlist` = crsPlanItemsJobsList, 
								`planItemVersion` = crsPlanItemsItemVersion,
								`planItemRequirement` = crsPlanItemsItemGrossRequired,
								`planRequirementSource` = crsPlanItemsItemSrcContainer,
								`planRequirementConsumer` = crsPlanItemsItemDestContainer, 
								`stockCacheRowID` = stkCrsStockRowID,
								`stockCacheRowConsumed` = 1,
								`stockItem` = stkCrsStockItem,
								`stockItemVersion` = stkCrsStockItemVersion,
								`stockItemShapeFormat` = stkCrsStockItemShapeFormat,
								`stockItemMultiplier` = stkCrsStockItemMultiplier,
								`stockItemUnitConversion` = stkCrsStockItemUnitConversionFactor,
								`stockItemPlanRequirement` = crsPlanItemsItemGrossRequired,
								`stockItemNaturalUnitsQuantity` = stkCrsStockItemNaturalUnits,
								`stockItemRawQuantity` = stkCrsItemStockAllocationInnerLocal,
								`stockItemAllocatedQuantity` = crsPlanItemsItemRequirementDelta,
								`stockItemAllocatedUnits` = stkCrsStockItemUnit,
								`stockItemRunningSum` = crsPlanItemsLocalLoopItemRunningSum,
								`stockItemRunningSumSpillOver` = 0,
								`stockItemStockRequirementDelta` = crsPlanItemsLocalLoopItemRequirementDelta, 
								`stockItemDestContainer` = stkCrsStockDestContainer,
								`stockItemProductionDate` = stkCrsStockItemProductionDate,
								`stockItemUseBy` = stkCrsStockItemUseBy,
								`stockItemTraceNumber` =  stkCrsStockItemTraceNumber;

							UPDATE
								`production`.`tblBOMaggregatePickingLists` tpl
							SET
								tpl.`stockCacheRowConsumed` = -1
							WHERE TRUE
								AND tpl.`id` > 0
								AND tpl.`stockCacheRowID` = stkCrsStockRowID;
						END IF;
						SET stkCrsItemStockAllocationComplete = FALSE;
                            
					END IF;
				END IF;

			END WHILE;

			CLOSE crsStockRowsCandidateStock; -- LOOP THROUGH ITEMS IN PLAN OPEN ROWS FOR PICKING LIST
			SET crsDone = FALSE;
        
		END IF;

	END WHILE;

	CLOSE crsPlanItemsOpenBucket; -- LOOP THROUGH ITEMS IN PLAN OPEN ROWS FOR PICKING LIST
    SET crsDone = FALSE;

-- #################################  FIND LARGER BUCKETS AND TRY TO FILL WITH SHAPE AND FORMAT STOCK USING ALL AVAILABLE STOCK  ######################################
-- ####################################################################################################################################################################
-- ####################################################################################################################################################################    
-- ####################################################################################################################################################################
	SELECT * FROM `production`.`tblBOMaggregatePickingLists`; -- #############   DISPLAY PICKING LIST  ####################################################################
-- ####################################################################################################################################################################
-- ####################################################################################################################################################################
    
END ;;

-- ========== related procedures (duplicates / variants) ==========
-- procSTKplanPickingListWithStock00, 30, 32, 34, 36, 38, 40: same picking logic,
--   different stock-mode branches; only procSTKplanPickingListWithStock extracted above.
-- BOMrecursive002FirstSweep, 003Modifiers..., 999Wrapper: full plan BOM pipeline;
--   explosion math is in BOMrecursive000AtLevelExpand (recursive CTE).
-- procSTKstockOUTprocess: stock availability query for OUT UI (joins tblunits by id).
-- Access client mirror: VB Access/modules/mdlUnits.bas (unitToUnitConversionFactor, shapeFormatMultiplier).
